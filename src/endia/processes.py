"""Processes: the programs running in a machine's Windows.

A process is named by its machine and its process ID. Windows reuses process IDs, so every route that names a process also takes
the time it started: when the process with that ID started at another time, the route refuses with ``NOT_FOUND`` rather than act
on the wrong process. Pass the ``started`` time exactly as a record of this SDK gave it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from . import _wire
from ._connection import Connection
from ._query import Query, segment


@dataclass(frozen=True, kw_only=True, slots=True)
class WindowBounds:
	"""A rectangle in physical pixels."""

	x: int
	y: int
	width: int
	height: int


@dataclass(frozen=True, kw_only=True, slots=True)
class ProcessWindow:
	"""One top-level window of a process."""

	handle: str
	"""The window's handle, as hexadecimal text."""

	title: str
	class_name: str
	visible: bool
	bounds: WindowBounds


@dataclass(frozen=True, kw_only=True, slots=True)
class ProcessSummary:
	"""One process as ``endia process ls`` lists it."""

	process_id: int
	parent_process_id: int
	session_id: int
	name: str
	user: str | None = None
	started: datetime | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ProcessDetail:
	"""One process in full: ``endia process inspect``."""

	process_id: int
	started: datetime
	session_id: int
	running: bool
	windows: list[ProcessWindow]
	parent_process_id: int | None = None
	name: str | None = None
	executable_path: str | None = None
	command_line: str | None = None
	user: str | None = None
	integrity_level: str | None = None
	"""``low``, ``medium``, ``high`` or ``system``."""

	exit_code: int | None = None
	"""The exit code of an ended process, known only for processes EastIndia started on the machine's current boot."""


@dataclass(frozen=True, kw_only=True, slots=True)
class ProcessStopResult:
	"""How a process stopped."""

	how: str
	"""``closed`` when it closed when asked, ``ended`` when it was ended, ``alreadyExited`` when it had ended before."""

	exit_code: int | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ProcessLogLine:
	"""One line that a process started detached wrote."""

	stream: str
	"""``stdout`` or ``stderr``."""

	line: str


class ProcessesClient:
	"""The process routes of a running machine: list, inspect, wait, stop and logs."""

	def __init__(self, connection: Connection) -> None:
		self._connection = connection

	def list(self, machine: str) -> list[ProcessSummary]:
		"""Lists every process running in a machine: ``endia process ls``."""
		return _wire.read(self._connection.read(_processes(machine)), list[ProcessSummary])

	def inspect(self, machine: str, process_id: int, *, started: datetime | None = None) -> ProcessDetail:
		"""Reads one process in full: ``endia process inspect``."""
		return _wire.read(self._connection.read(_process(machine, process_id) + str(_started(Query(), started))), ProcessDetail)

	def wait(self, machine: str, process_id: int, *, timeout: int | None = None, started: datetime | None = None) -> int | None:
		"""Waits until a process ends, and gives its exit code: ``endia process wait``.

		The service answers only when the wait ends, so no time limit of the client's own applies.

		Args:
			timeout: The longest wait in seconds; None waits without limit. When it passes first, the wait is refused with
				``TIMEOUT`` and the process keeps running.

		Returns:
			The exit code, or None when the service does not know it.
		"""
		query = _started(Query().add("timeout", timeout), started)
		answer = self._connection.change("POST", _process(machine, process_id) + "/wait" + str(query), timed=False)
		exit_code: int | None = answer.json().get("exitCode")
		return exit_code

	def stop(self, machine: str, process_id: int, *, timeout: int | None = None, started: datetime | None = None) -> ProcessStopResult:
		"""Stops a process as ``docker stop`` stops a container: ``endia process stop``.

		The service asks the process to close, by a close message to each of its top-level windows or a ``CTRL_BREAK`` event for a
		console process, waits up to ``timeout`` seconds (10 by default), then ends it with exit code 1. A timeout of 0 ends it
		at once.
		"""
		query = _started(Query().add("t", timeout), started)
		answer = self._connection.change("POST", _process(machine, process_id) + "/stop" + str(query), timed=False)
		return _wire.read(answer.json(), ProcessStopResult)

	def logs(self, machine: str, process_id: int, *, follow: bool = False) -> Iterator[ProcessLogLine]:
		"""Reads the saved output of a process that a detached exec started on the current boot: ``endia process logs``.

		Args:
			follow: Keep reading each new line until the process has ended.
		"""
		for line in self._connection.lines(_process(machine, process_id) + "/logs" + str(Query().flag("follow", follow))):
			if line:
				yield _wire.read_json_line(line, ProcessLogLine)


def _processes(machine: str) -> str:
	return f"/v1/machines/{segment(machine)}/processes"


def _process(machine: str, process_id: int) -> str:
	return f"{_processes(machine)}/{process_id}"


def _started(query: Query, started: datetime | None) -> Query:
	return query.add("started", _wire.format_time(started) if started is not None else None)
