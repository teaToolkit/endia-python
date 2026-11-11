"""Machines: the Hyper-V virtual machines that fleets make from images, as Docker's containers are made from its images."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from . import _wire
from ._connection import Connection
from ._query import Query, segment
from .errors import ApiError
from .operations import OperationHandle, OperationsClient, handle_of
from .purposes import MachinePurpose, MachinePurposeResult, PurposeDetail, PurposeSummary, purpose_body

_MACHINES = "/v1/machines"
_PURPOSE = "/purpose"


class MachineState:
	"""The states of a machine."""

	CREATED = "created"
	RUNNING = "running"
	RESTARTING = "restarting"
	EXITED = "exited"
	REMOVING = "removing"


class MachineHealth:
	"""The health of a machine, from its service's health check."""

	NONE = "none"
	"""The machine has no health check."""

	STARTING = "starting"
	HEALTHY = "healthy"
	UNHEALTHY = "unhealthy"


@dataclass(frozen=True, kw_only=True, slots=True)
class PortBinding:
	"""A port of the machine published on the host."""

	host_ip: str
	published: int
	target: int


@dataclass(frozen=True, kw_only=True, slots=True)
class MachineSummary:
	"""One machine as ``endia ps`` lists it."""

	id: str
	"""The Hyper-V virtual machine ID, a lower-case GUID."""

	name: str
	fleet: str
	service: str
	index: int
	"""The machine's number among its service's machines, counting from 1."""

	image: str
	image_digest: str
	command: str
	created: datetime
	state: str
	"""One of ``MachineState``."""

	status: str
	"""The state as ``endia ps`` prints it, such as ``Up 5 minutes (healthy)``."""

	ready: bool
	"""Whether the machine is ready: its virtual machine runs, it was configured in this boot, and nothing has failed since."""

	health: str
	"""One of ``MachineHealth``."""

	ports: list[PortBinding]
	exit_code: int | None = None
	purpose: PurposeSummary | None = None
	"""What the machine is being used for: its own purpose, else its fleet's, else None."""

	purpose_source: str | None = None
	"""Where ``purpose`` comes from, one of ``PurposeSource``, or None when there is none."""


@dataclass(frozen=True, kw_only=True, slots=True)
class MachineCardDetail:
	"""One network card of a machine."""

	network: str
	switch_name: str
	mac_address: str
	ipv4_address: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class MachineVolumeDetail:
	"""A named volume attached to a machine."""

	name: str
	target: str
	read_only: bool


@dataclass(frozen=True, kw_only=True, slots=True)
class MainProcessDetail:
	"""The machine's main process: the program its service's command runs."""

	pid: int
	started: datetime
	exit_code: int | None = None
	exited: datetime | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class HealthCheckRunDetail:
	"""One run of a machine's health check."""

	start: datetime
	end: datetime
	exit_code: int
	output: str


@dataclass(frozen=True, kw_only=True, slots=True)
class MachineHealthDetail:
	"""A machine's health and its last health check runs."""

	status: str
	failing_streak: int
	log: list[HealthCheckRunDetail]


@dataclass(frozen=True, kw_only=True, slots=True)
class MachineDetail:
	"""One machine in full: ``endia inspect``."""

	id: str
	name: str
	fleet: str
	service: str
	index: int
	image: str
	image_digest: str
	definition: dict[str, Any]
	"""The service's resolved definition the machine was made from."""

	created: datetime
	computer_name: str
	memory_bytes: int
	startup_memory_bytes: int
	processors: int
	cards: list[MachineCardDetail]
	volumes: list[MachineVolumeDetail]
	ports: list[PortBinding]
	state: str
	ready: bool
	"""Whether the machine is ready, as ``MachineSummary.ready`` says. When its image signs a user in by itself, ready also means that
	user was signed in with the desktop helper ready."""

	stopped_by_user: bool
	restart_count: int
	health: MachineHealthDetail
	addresses: dict[str, str]
	"""The machine's address on each of its networks."""

	desktop_ready: bool
	started: datetime | None = None
	exited: datetime | None = None
	exit_code: int | None = None
	boot_id: str | None = None
	ready_boot_id: str | None = None
	main_process: MainProcessDetail | None = None
	last_error: ApiError | None = None
	desktop_user: str | None = None
	uac_prompt_automation: bool | None = None
	purpose: PurposeDetail | None = None
	"""What the machine is being used for, in full: its own purpose, else its fleet's, else None."""

	purpose_source: str | None = None
	"""Where ``purpose`` comes from, one of ``PurposeSource``, or None when there is none."""


@dataclass(frozen=True, kw_only=True, slots=True)
class MachineLogLine:
	"""One line of a machine's log."""

	time: datetime
	stream: str
	"""``stdout`` or ``stderr``."""

	line: str
	machine: str | None = None
	"""The machine that wrote the line, in a fleet's log."""


@dataclass(frozen=True, kw_only=True, slots=True)
class MachinesResult:
	"""The machines a start or a stop acted on."""

	machines: list[str]


@dataclass(frozen=True, kw_only=True, slots=True)
class MachineRestartResult:
	"""A restart of Windows in a machine."""

	name: str
	boot_id: str
	turned_off: bool
	"""Whether the virtual machine was turned off rather than Windows restarting in an orderly way."""

	previous_boot_id: str | None = None


class MachinesClient:
	"""The machine routes: list, inspect, logs and purpose, which answer at once, and start, stop and restart, which start operations.

	A machine is named by its name or by a unique prefix of its ID.
	"""

	def __init__(self, connection: Connection, operations: OperationsClient) -> None:
		self._connection = connection
		self._operations = operations

	def list(self, *, all: bool = False, filters: Mapping[str, str | Sequence[str]] | None = None) -> list[MachineSummary]:
		"""Lists the running machines, or with ``all`` every machine: ``endia ps``.

		Args:
			filters: Docker-style filters, such as ``{"name": "classroom-"}`` or ``{"status": "exited"}``.
		"""
		query = Query().flag("all", all).filters(filters)
		return _wire.read(self._connection.read(_MACHINES + str(query)), list[MachineSummary])

	def inspect(self, machine: str) -> MachineDetail:
		"""Reads one machine in full: ``endia inspect``."""
		return _wire.read(self._connection.read(_path(machine)), MachineDetail)

	def purpose(self, machine: str) -> MachinePurpose:
		"""Reads a machine's own purpose with every change to it, and the purpose in effect for it: ``endia purpose show``."""
		return _wire.read(self._connection.read(_path(machine) + _PURPOSE), MachinePurpose)

	def set_purpose(self, machine: str, title: str, detail: str | None = None) -> MachinePurposeResult:
		"""Replaces a machine's own purpose, which overrides its fleet's for this machine only: ``endia purpose set``.

		Args:
			title: What the machine is being used for, in one line of 1 to 200 characters.
			detail: Markdown notes of at most 65536 bytes in UTF-8. None clears the detail stored before.

		Raises:
			ValueError: The title has a line break, or the detail is too long. Nothing is sent then.
		"""
		body = purpose_body(title, detail)
		answer = self._connection.change("PUT", _path(machine) + _PURPOSE, json_body=body)
		return _wire.read(answer.json(), MachinePurposeResult)

	def clear_purpose(self, machine: str) -> MachinePurposeResult:
		"""Removes a machine's own purpose, which returns it to its fleet's: ``endia purpose clear``."""
		return _wire.read(self._connection.change("DELETE", _path(machine) + _PURPOSE).json(), MachinePurposeResult)

	def start(self, machine: str) -> OperationHandle[MachinesResult]:
		"""Starts an exited or created machine: ``endia start``."""
		return handle_of(self._operations, self._connection.change("POST", _path(machine) + "/start"), MachinesResult)

	def stop(self, machine: str, *, timeout: int | None = None) -> OperationHandle[MachinesResult]:
		"""Stops a machine: ``endia stop``.

		Args:
			timeout: The seconds Windows has to shut down before the machine is turned off; None takes the service's
				``stop_grace_period``, else the host's ``stopTimeoutSeconds``.
		"""
		answer = self._connection.change("POST", _path(machine) + "/stop" + str(Query().add("t", timeout)))
		return handle_of(self._operations, answer, MachinesResult)

	def restart(self, machine: str, *, timeout: int | None = None, hard: bool = False) -> OperationHandle[MachineRestartResult]:
		"""Restarts Windows in a machine: ``endia restart``. The operation ends once Windows has started again and is ready.

		Args:
			timeout: The seconds Windows has to shut down before the machine is turned off.
			hard: Turn the machine off at once instead of shutting Windows down.
		"""
		answer = self._connection.change("POST", _path(machine) + "/restart" + str(Query().add("t", timeout).flag("hard", hard)))
		return handle_of(self._operations, answer, MachineRestartResult)

	def logs(
		self,
		machine: str,
		*,
		follow: bool = False,
		tail: int | str | None = None,
		since: str | None = None,
		until: str | None = None,
	) -> Iterator[MachineLogLine]:
		"""Reads a machine's log: ``endia logs``.

		Args:
			follow: Keep reading new lines until the iteration is abandoned.
			tail: Start with only this many of the last lines, or ``all``.
			since: The earliest time of a line, as ``docker logs --since`` reads it: an RFC 3339 time or a duration such as ``10m``.
			until: The latest time of a line, in the same forms.
		"""
		query = log_query(follow=follow, tail=tail, since=since, until=until)
		for line in self._connection.lines(_path(machine) + "/logs" + str(query)):
			if line:
				yield _wire.read_json_line(line, MachineLogLine)


def log_query(*, follow: bool, tail: int | str | None, since: str | None, until: str | None) -> Query:
	"""Builds the query of a log route: which lines to read, and whether to keep reading new ones."""
	return Query().flag("follow", follow).add("tail", tail).add("since", since).add("until", until)


def _path(machine: str) -> str:
	return f"{_MACHINES}/{segment(machine)}"
