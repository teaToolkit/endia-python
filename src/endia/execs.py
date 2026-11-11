"""Execs: programs run in a running machine, as ``docker exec`` runs them in a container.

An exec takes Docker's three steps: create it in a machine, start it attached or detached, and read its state. Attached, a
WebSocket carries the program's streams until it ends, and the exec's state then gives its exit code in full, beyond 255.
Detached, the program runs on and its process ID is returned; ``ProcessesClient`` waits for it and reads its saved output.

Without a console, each WebSocket message is one frame in Docker's multiplexed format: an eight-byte header, then the payload.
Byte 0 of the header is the stream (0 standard input, 1 standard output, 2 standard error), bytes 1 to 3 are zero, and bytes 4 to
7 are the payload's length as a big-endian unsigned integer. A standard-input frame with an empty payload closes the program's
standard input. With a console, messages carry the console's bytes unchanged in both directions.
"""

from __future__ import annotations

import enum
import struct
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType

from websockets.exceptions import ConnectionClosedError

from . import _wire
from ._connection import Connection, classify
from ._query import Query, segment
from .errors import EastIndiaApiError, EastIndiaConnectionError, EastIndiaError, ExecTimeoutError

_FRAME_HEADER = struct.Struct(">B3xI")
"""A frame's header: the stream's byte, three zero bytes, and the payload's length as a big-endian unsigned 32-bit integer."""


class StreamKind(enum.IntEnum):
	"""The stream a frame carries bytes for; the value is the frame's first byte."""

	STDIN = 0
	STDOUT = 1
	STDERR = 2


def encode_frame(kind: StreamKind, payload: bytes) -> bytes:
	"""Writes one frame in Docker's multiplexed format."""
	return _FRAME_HEADER.pack(kind, len(payload)) + payload


def decode_frame(message: bytes) -> tuple[StreamKind, bytes] | None:
	"""Reads one WebSocket message as one frame, or gives None when the message is not exactly one frame: shorter than a header,
	an unknown stream, non-zero padding, or a length that differs from the payload's.
	"""
	if len(message) < _FRAME_HEADER.size or message[0] > StreamKind.STDERR or any(message[1:4]):
		return None
	_, length = _FRAME_HEADER.unpack_from(message)
	if length != len(message) - _FRAME_HEADER.size:
		return None
	return StreamKind(message[0]), message[_FRAME_HEADER.size :]


@dataclass(frozen=True, kw_only=True, slots=True)
class ExecDetail:
	"""An exec's state."""

	id: str
	"""The exec's ID: 32 lower-case hexadecimal digits."""

	machine: str
	cmd: list[str]
	tty: bool
	detached: bool
	running: bool
	boot_id: str | None = None
	user: str | None = None
	working_dir: str | None = None
	process_id: int | None = None
	started: datetime | None = None
	exit_code: int | None = None
	"""The program's exit code, in full; None while it runs, and when a restart of the machine ended it."""

	exited: datetime | None = None
	start_error: str | None = None
	"""Why the program could not start. A program that was not found ends with exit code 127, and one that was found but could
	not start with 126."""


@dataclass(frozen=True, kw_only=True, slots=True)
class ExecStarted:
	"""A program started detached."""

	process_id: int
	started: datetime
	"""When the process started, which the process routes take as ``started``."""


@dataclass(frozen=True, kw_only=True, slots=True)
class ExecResult:
	"""A program that ran attached to its end: its exit code and everything it wrote."""

	exec_id: str
	exit_code: int | None
	"""The program's exit code, in full; None when a restart of the machine ended the program."""

	stdout: bytes
	stderr: bytes
	start_error: str | None = None
	"""Why the program could not start, with exit code 126 or 127."""


class ExecSession:
	"""An attached exec: the program's output as it arrives, and its standard input.

	Iterating gives each piece of output as the stream it came from and its bytes, until the program has ended and the service
	closes the WebSocket. Closing the session closes the WebSocket; the program keeps running if it has not ended.
	"""

	def __init__(self, execs: ExecsClient, connection: Connection, id: str, tty: bool) -> None:
		self.id = id
		"""The exec's ID."""
		self._execs = execs
		self._tty = tty
		self._address = connection.settings.address
		self._owner = ExitStack()
		self._socket = connection.websocket(_path(id) + "/start", self._owner)

	def __iter__(self) -> Iterator[tuple[StreamKind, bytes]]:
		try:
			for message in self._socket:
				data = message.encode() if isinstance(message, str) else message
				if self._tty:
					yield StreamKind.STDOUT, data
				elif (frame := decode_frame(data)) is not None:
					yield frame
		except (ConnectionClosedError, OSError) as failed:
			raise EastIndiaConnectionError(self._address, classify(failed)) from failed

	def send(self, data: bytes) -> None:
		"""Sends bytes to the program's standard input, which the exec must have been created with ``attach_stdin``."""
		self._socket.send(data if self._tty else encode_frame(StreamKind.STDIN, data))

	def close_input(self) -> None:
		"""Closes the program's standard input, so that a program reading it to its end can finish."""
		if not self._tty:
			self._socket.send(encode_frame(StreamKind.STDIN, b""))

	def inspect(self) -> ExecDetail:
		"""Reads the exec's state; once the output has ended, it holds the exit code."""
		return self._execs.inspect(self.id)

	def close(self) -> None:
		"""Closes the WebSocket."""
		self._owner.close()

	def __enter__(self) -> ExecSession:
		return self

	def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None) -> None:
		self.close()


class ExecsClient:
	"""The exec routes: create, start attached or detached, inspect and resize; and ``run`` and ``run_detached``, which take
	the steps in turn.
	"""

	def __init__(self, connection: Connection) -> None:
		self._connection = connection

	def run(
		self,
		machine: str,
		cmd: Sequence[str],
		*,
		stdin: bytes | None = None,
		user: str | None = None,
		working_dir: str | None = None,
		env: Sequence[str] = (),
		on_output: Callable[[StreamKind, bytes], None] | None = None,
		timeout: float | None = None,
	) -> ExecResult:
		"""Runs a program in a running machine to its end, and gives its exit code and output: ``endia exec``.

		Args:
			cmd: The program followed by its arguments. Windows searches ``PATH`` for the program.
			stdin: Bytes for the program's standard input, which is then closed; None gives the program no standard input.
			user: The local account to run as; None takes the service's default, else the image's.
			working_dir: The folder to start in; None takes the service's default, else the image's.
			env: Extra variables, each ``NAME=value``, laid over the machine's main-process environment.
			on_output: Called with each piece of output as it arrives, as well as collecting it.
			timeout: The seconds the program may run; None lets it run to its end. When they pass, the program and every process it
				started are ended at once, as ``endia process stop -t 0 --tree`` ends them, and ``ExecTimeoutError`` is raised.

		Raises:
			EastIndiaApiError: ``MACHINE_NOT_RUNNING`` when the machine is not running and ready.
			ExecTimeoutError: The program did not end within ``timeout``; the error holds its output until then.
		"""
		exec_id = self.create(machine, cmd, user=user, working_dir=working_dir, env=env, attach_stdin=stdin is not None)
		output: dict[StreamKind, list[bytes]] = {StreamKind.STDOUT: [], StreamKind.STDERR: []}
		timed_out = threading.Event()
		with self.attach(exec_id) as session:
			timer = threading.Timer(timeout, self._end, args=(machine, session, timed_out)) if timeout is not None else None
			if timer is not None:
				timer.daemon = True
				timer.start()
			try:
				if stdin is not None:
					# The input is sent from a thread of its own, so that a program which writes before it has read all of its input
					# never waits on a client that is still sending.
					threading.Thread(target=_send_all, args=(session, stdin), daemon=True).start()
				for kind, data in session:
					output[kind].append(data)
					if on_output is not None:
						on_output(kind, data)
			except EastIndiaConnectionError:
				if not timed_out.is_set():
					raise
			finally:
				if timer is not None:
					timer.cancel()
		if timed_out.is_set():
			raise ExecTimeoutError(exec_id, timeout or 0, b"".join(output[StreamKind.STDOUT]), b"".join(output[StreamKind.STDERR]))
		ended = self.inspect(exec_id)
		return ExecResult(
			exec_id=exec_id,
			exit_code=ended.exit_code,
			stdout=b"".join(output[StreamKind.STDOUT]),
			stderr=b"".join(output[StreamKind.STDERR]),
			start_error=ended.start_error,
		)

	def _end(self, machine: str, session: ExecSession, timed_out: threading.Event) -> None:
		"""Ends a program whose time is up, with the processes it started, then closes its session so that ``run`` stops reading."""
		timed_out.set()
		try:
			running = self.inspect(session.id)
			if running.running and running.process_id is not None:
				query = Query().add("t", 0).flag("tree", True)
				if running.started is not None:
					query = query.add("started", _wire.format_time(running.started))
				path = f"/v1/machines/{segment(machine)}/processes/{running.process_id}/stop"
				self._connection.change("POST", path + str(query), timed=False)
		except (EastIndiaApiError, EastIndiaError):
			# The program may have ended as its time ran out; closing the session is all that is left to do.
			pass
		finally:
			session.close()

	def run_detached(
		self,
		machine: str,
		cmd: Sequence[str],
		*,
		user: str | None = None,
		working_dir: str | None = None,
		env: Sequence[str] = (),
	) -> ExecStarted:
		"""Starts a program in a running machine and returns at once: ``endia exec -d``.

		The service keeps the program's output for ``ProcessesClient.logs``, and ``ProcessesClient.wait`` waits for its end.
		"""
		return self.start_detached(self.create(machine, cmd, user=user, working_dir=working_dir, env=env))

	def create(
		self,
		machine: str,
		cmd: Sequence[str],
		*,
		user: str | None = None,
		working_dir: str | None = None,
		env: Sequence[str] = (),
		tty: bool = False,
		attach_stdin: bool = False,
		console_size: tuple[int, int] | None = None,
	) -> str:
		"""Creates an exec in a running machine, and gives its ID; nothing runs yet.

		Args:
			tty: Give the program a Windows pseudo console.
			attach_stdin: Keep the program's standard input open for what the caller sends.
			console_size: The console's rows and columns; None takes the pseudo console's default.
		"""
		body = {
			"cmd": list(cmd),
			"user": user,
			"workingDir": working_dir,
			"env": list(env),
			"tty": tty,
			"attachStdin": attach_stdin,
			"consoleSize": list(console_size) if console_size is not None else None,
		}
		answer = self._connection.change(
			"POST", f"/v1/machines/{segment(machine)}/exec", json_body={k: v for k, v in body.items() if v is not None}
		)
		exec_id: str = answer.json()["id"]
		return exec_id

	def attach(self, id: str, *, tty: bool = False) -> ExecSession:
		"""Starts an exec attached, over a WebSocket.

		Args:
			tty: Whether the exec was created with a console, whose bytes travel unframed.
		"""
		return ExecSession(self, self._connection, id, tty)

	def start_detached(self, id: str) -> ExecStarted:
		"""Starts an exec detached, and gives the program's process ID."""
		return _wire.read(self._connection.change("POST", _path(id) + "/start", json_body={"detach": True}).json(), ExecStarted)

	def inspect(self, id: str) -> ExecDetail:
		"""Reads an exec's state, named by its ID or a unique prefix of it."""
		return _wire.read(self._connection.read(_path(id)), ExecDetail)

	def resize(self, id: str, rows: int, columns: int) -> None:
		"""Resizes a running exec's pseudo console."""
		self._connection.change("POST", _path(id) + "/resize" + str(Query().add("h", rows).add("w", columns)))


def _send_all(session: ExecSession, data: bytes) -> None:
	try:
		if data:
			session.send(data)
		session.close_input()
	except Exception:
		# The program has ended, or the session has closed; the output loop reports what the caller needs to know.
		return


def _path(id: str) -> str:
	return f"/v1/execs/{segment(id)}"
