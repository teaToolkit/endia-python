"""Fleets: the machines, networks and volumes of one Compose project, which ``compose up`` brings to the state its model describes.

The SDK brings a fleet up from the project's resolved model, the JSON that ``endia compose config --format json`` prints, given
as a mapping. It does not read Compose files itself: the CLI's loader, with its interpolation, merging and checks, stays the one
reader of them. A model the caller writes as a mapping works too, when every build and volume is in long syntax and every path
in it is absolute.
"""

from __future__ import annotations

import builtins
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import IO, Any

from . import _wire
from ._connection import Connection
from ._fleet_archive import pack_fleet, selected_services
from ._query import Query, segment
from .errors import MachineFailedError
from .machines import MachineHealth, MachineLogLine, MachineState, MachineSummary, log_query
from .operations import OperationHandle, OperationsClient, handle_of
from .purposes import FleetPurpose, FleetPurposeResult, PurposeDetail, PurposeSummary, purpose_body

_FLEETS = "/v1/fleets"
_PURPOSE = "/purpose"

# A wait reads the fleet once a second, as endia compose up --wait does.
_WAIT_POLL_SECONDS = 1.0


@dataclass(frozen=True, kw_only=True, slots=True)
class FleetSummary:
	"""One fleet as ``endia compose ls`` lists it."""

	name: str
	status: str
	"""How many machines are in each state, in alphabetical order of the state, such as ``exited(1), running(3)``."""

	config_files: list[str]
	purpose: PurposeSummary | None = None
	"""What the fleet is being used for, or None when it has no purpose."""


@dataclass(frozen=True, kw_only=True, slots=True)
class FleetNetworkDetail:
	"""One of a fleet's networks."""

	key: str
	"""The network's key in the model, such as ``backend``."""

	name: str
	switch_name: str
	switch_id: str
	subnet: str
	internal: bool
	addresses: dict[str, str]
	"""Each machine's address on the network."""


@dataclass(frozen=True, kw_only=True, slots=True)
class FleetDetail:
	"""One fleet with its machines: ``endia compose ps``."""

	name: str
	config_files: list[str]
	project_directory: str
	model: dict[str, Any]
	"""The resolved model the fleet was last brought up from."""

	networks: list[FleetNetworkDetail]
	volumes: list[str]
	machines: list[MachineSummary]
	created: datetime
	updated: datetime
	purpose: PurposeDetail | None = None
	"""What the fleet is being used for, in full, or None when it has no purpose. Each machine without a purpose of its own has it."""


@dataclass(frozen=True, kw_only=True, slots=True)
class DryRunAction:
	"""One action that ``compose up`` would take."""

	action: str
	"""``createNetwork``, ``createVolume``, ``build``, ``create``, ``recreate``, ``start``, ``remove`` or ``keep``."""

	target: str
	reason: str


@dataclass(frozen=True, kw_only=True, slots=True)
class FleetDryRun:
	"""What ``compose up`` would do, and whether the host has the memory for it."""

	actions: list[DryRunAction]
	memory_required_bytes: int
	"""The starting memory of the machines the plan would start."""

	memory_available_bytes: int
	"""The host's free memory less the reserve the host keeps."""


@dataclass(frozen=True, kw_only=True, slots=True)
class MachineOutcome:
	"""What ``compose up`` did to one machine."""

	name: str
	service: str
	action: str
	"""``created``, ``recreated``, ``started``, ``removed`` or ``unchanged``."""

	state: str
	health: str


@dataclass(frozen=True, kw_only=True, slots=True)
class NetworkOutcome:
	"""What ``compose up`` did to one network."""

	name: str
	subnet: str
	action: str


@dataclass(frozen=True, kw_only=True, slots=True)
class VolumeOutcome:
	"""What ``compose up`` did to one named volume."""

	name: str
	action: str


@dataclass(frozen=True, kw_only=True, slots=True)
class FleetUpResult:
	"""What ``compose up`` did."""

	fleet: str
	machines: list[MachineOutcome]
	networks: list[NetworkOutcome]
	volumes: list[VolumeOutcome]
	warnings: list[str]


@dataclass(frozen=True, kw_only=True, slots=True)
class FleetDownResult:
	"""What ``compose down`` removed."""

	fleet: str
	machines: list[str]
	networks: list[str]
	volumes: list[str]
	warnings: list[str]


@dataclass(frozen=True, kw_only=True, slots=True)
class FleetMachinesResult:
	"""The machines that ``compose stop``, ``start`` or ``restart`` acted on."""

	fleet: str
	machines: list[str]
	warnings: list[str]


class FleetsClient:
	"""The fleet routes: up and its dry run, down, stop, start, restart, list, inspect and logs, a fleet's purpose, and a wait for a fleet's
	machines to be ready and healthy. A fleet is named by its project name.
	"""

	def __init__(self, connection: Connection, operations: OperationsClient) -> None:
		self._connection = connection
		self._operations = operations

	def up(
		self,
		model: Mapping[str, Any],
		*,
		services: Sequence[str] = (),
		scale: Mapping[str, int] | None = None,
		build: str = "missing",
		recreate: str = "changed",
		remove_orphans: bool = False,
		timeout: int | None = None,
		config_files: Sequence[str] = (),
		project_directory: str | None = None,
		purpose: str | None = None,
		purpose_detail: str | None = None,
	) -> OperationHandle[FleetUpResult]:
		"""Starts bringing a fleet to the state its resolved model describes: ``endia compose up -d``.

		The model, the build context of each selected service that builds, and the source of every bind mount are sent as one tar
		archive. The fleet's name is the model's ``name``.

		The operation succeeds once every machine it created, recreated or started is ready: its virtual machine runs and it was
		configured in this boot, and when its image signs a user in by itself, that user is signed in with the desktop helper ready.
		A machine whose user is not signed in within ``desktopReadyTimeoutSeconds`` fails the operation with ``TIMEOUT``. A machine
		that was already running and is kept unchanged is not checked again. Health checks are not waited for; ``wait`` waits for
		them, and for kept machines, as ``endia compose up --wait`` does.

		Args:
			model: The resolved model, as ``endia compose config --format json`` prints it.
			services: The services to bring up, with every service they depend on; none brings up every service.
			scale: The number of machines of some services, whatever the model says.
			build: When to build images: ``missing``, ``always`` or ``never``.
			recreate: When to recreate machines: ``changed``, ``force`` or ``never``.
			remove_orphans: Remove machines of services no longer in the model, instead of naming them in a warning.
			timeout: The seconds Windows has to shut down in machines being recreated or removed.
			config_files: The Compose files' paths, which the fleet record keeps and ``compose ls`` shows.
			project_directory: The folder relative paths were resolved against, which the fleet record keeps.
			purpose: The fleet's purpose, what it is being used for, in one line of 1 to 200 characters: ``--purpose``. The service
				stores it, replacing any purpose the fleet has, before any other work. None leaves the fleet's purpose as it is.
			purpose_detail: Markdown notes for that purpose, at most 65536 bytes in UTF-8: the text of the file ``--purpose-file``
				names. It needs ``purpose``.

		Raises:
			ValueError: The model has no name, or is not in resolved form, or names a service it does not have; or ``purpose_detail``
				is given without ``purpose``, ``purpose`` has a line break, or ``purpose_detail`` is too long. Nothing is sent then.
			NotADirectoryError: A build context is not a folder.
			FileNotFoundError: A bind mount's source does not exist.
		"""
		query = _up_query(
			services=services,
			scale=scale,
			build=build,
			recreate=recreate,
			remove_orphans=remove_orphans,
			timeout=timeout,
			config_files=config_files,
			project_directory=project_directory,
			dry_run=False,
		)
		with _archive(model, services, _purpose(purpose, purpose_detail)) as archive:
			answer = self._connection.change("PUT", _path(_name(model)) + str(query), archive=archive, timed=False)
		return handle_of(self._operations, answer, FleetUpResult)

	def dry_run(
		self,
		model: Mapping[str, Any],
		*,
		services: Sequence[str] = (),
		scale: Mapping[str, int] | None = None,
		build: str = "missing",
		recreate: str = "changed",
		remove_orphans: bool = False,
	) -> FleetDryRun:
		"""Asks what ``up`` would do, changing nothing. The arguments are those of ``up``."""
		query = _up_query(
			services=services,
			scale=scale,
			build=build,
			recreate=recreate,
			remove_orphans=remove_orphans,
			timeout=None,
			config_files=(),
			project_directory=None,
			dry_run=True,
		)
		with _archive(model, services, None) as archive:
			answer = self._connection.change("PUT", _path(_name(model)) + str(query), archive=archive, timed=False)
		return _wire.read(answer.json(), FleetDryRun)

	def down(
		self, fleet: str, *, volumes: bool = False, remove_orphans: bool = False, timeout: int | None = None
	) -> OperationHandle[FleetDownResult]:
		"""Starts removing a fleet's machines and networks: ``endia compose down``. Images are kept.

		Args:
			volumes: Also delete the named volumes the fleet declares.
			remove_orphans: Also remove machines of services no longer in the fleet's model.
			timeout: The seconds Windows has to shut down in each machine before it is turned off.
		"""
		query = Query().flag("volumes", volumes).flag("removeOrphans", remove_orphans).add("timeout", timeout)
		return handle_of(self._operations, self._connection.change("DELETE", _path(fleet) + str(query)), FleetDownResult)

	def stop(self, fleet: str, *, services: Sequence[str] = (), timeout: int | None = None) -> OperationHandle[FleetMachinesResult]:
		"""Starts stopping a fleet's machines, dependants first: ``endia compose stop``."""
		return self._act(fleet, "stop", Query().each("services", services).add("timeout", timeout))

	def start(self, fleet: str, *, services: Sequence[str] = ()) -> OperationHandle[FleetMachinesResult]:
		"""Starts a fleet's existing machines in dependency order: ``endia compose start``. It creates nothing."""
		return self._act(fleet, "start", Query().each("services", services))

	def restart(self, fleet: str, *, services: Sequence[str] = (), timeout: int | None = None) -> OperationHandle[FleetMachinesResult]:
		"""Starts restarting Windows in a fleet's machines: ``endia compose restart``."""
		return self._act(fleet, "restart", Query().each("services", services).add("timeout", timeout))

	def list(self, *, all: bool = False) -> list[FleetSummary]:
		"""Lists the fleets with a running or restarting machine, or with ``all`` every fleet: ``endia compose ls``."""
		return _wire.read(self._connection.read(_FLEETS + str(Query().flag("all", all))), list[FleetSummary])

	def inspect(self, fleet: str) -> FleetDetail:
		"""Reads one fleet with its machines: ``endia compose ps``."""
		return _wire.read(self._connection.read(_path(fleet)), FleetDetail)

	def purpose(self, fleet: str) -> FleetPurpose:
		"""Reads a fleet's purpose with every change to it while the fleet has existed: ``endia purpose show``."""
		return _wire.read(self._connection.read(_path(fleet) + _PURPOSE), FleetPurpose)

	def set_purpose(self, fleet: str, title: str, detail: str | None = None) -> FleetPurposeResult:
		"""Replaces a fleet's whole purpose, which its machines without a purpose of their own have: ``endia purpose set``.

		Args:
			title: What the fleet is being used for, in one line of 1 to 200 characters.
			detail: Markdown notes of at most 65536 bytes in UTF-8. None clears the detail stored before.

		Raises:
			ValueError: The title has a line break, or the detail is too long. Nothing is sent then.
		"""
		body = purpose_body(title, detail)
		answer = self._connection.change("PUT", _path(fleet) + _PURPOSE, json_body=body)
		return _wire.read(answer.json(), FleetPurposeResult)

	def clear_purpose(self, fleet: str) -> FleetPurposeResult:
		"""Removes a fleet's purpose: ``endia purpose clear``. Clearing a fleet without a purpose changes nothing."""
		return _wire.read(self._connection.change("DELETE", _path(fleet) + _PURPOSE).json(), FleetPurposeResult)

	def wait(self, fleet: str, *, services: Sequence[str] = (), timeout: float | None = None) -> builtins.list[MachineSummary]:
		"""Waits until a fleet's machines are running and ready, and healthy when they have a health check, as
		``endia compose up --wait`` waits once its operation has succeeded.

		``up(...).result()`` already returns only once every machine it created, recreated or started was made ready, which
		includes the desktop user being signed in with the desktop helper ready when the image signs a user in by itself. This wait
		adds what ``--wait`` adds: it checks every selected machine, kept ones too, waits for health checks to pass, and fails when
		a machine exits or becomes unhealthy.

		Args:
			services: The services whose machines to wait for, with every service they depend on, as ``up`` takes them; none waits
				for every service.
			timeout: The longest wait in seconds, as ``--wait-timeout``; None waits without limit.

		Returns:
			The machines waited for, as last read.

		Raises:
			ValueError: A named service is not in the fleet's model.
			MachineFailedError: A machine exited or became unhealthy.
			TimeoutError: Some machine was not yet running, ready and healthy when the time-out passed. The machines carry on.
		"""
		deadline = None if timeout is None else time.monotonic() + timeout
		selected: set[str] | None = None
		while True:
			detail = self.inspect(fleet)
			if selected is None:
				selected = selected_services(detail.model, services)
			machines = [machine for machine in detail.machines if machine.service in selected]
			failed = next((machine for machine in machines if _has_failed(machine)), None)
			if failed is not None:
				raise MachineFailedError(failed)
			if all(_is_up(machine) for machine in machines):
				return machines
			if deadline is not None and time.monotonic() >= deadline:
				raise TimeoutError(f"timed out after {timeout} seconds waiting for the machines to be running and healthy")
			time.sleep(_WAIT_POLL_SECONDS)

	def logs(
		self,
		fleet: str,
		*,
		services: Sequence[str] = (),
		index: int | None = None,
		follow: bool = False,
		tail: int | str | None = None,
		since: str | None = None,
		until: str | None = None,
	) -> Iterator[MachineLogLine]:
		"""Reads the logs of a fleet's machines, each line naming its machine: ``endia compose logs``.

		Args:
			services: Read only these services' machines.
			index: Read only the machine with this number in each service.

		The other arguments are those of ``MachinesClient.logs``.
		"""
		query = log_query(follow=follow, tail=tail, since=since, until=until).each("services", services).add("index", index)
		for line in self._connection.lines(_path(fleet) + "/logs" + str(query)):
			if line:
				yield _wire.read_json_line(line, MachineLogLine)

	def _act(self, fleet: str, action: str, query: Query) -> OperationHandle[FleetMachinesResult]:
		return handle_of(self._operations, self._connection.change("POST", f"{_path(fleet)}/{action}{query}"), FleetMachinesResult)


def _archive(model: Mapping[str, Any], services: Sequence[str], purpose: dict[str, Any] | None) -> IO[bytes]:
	return pack_fleet(model, _built(model, services), purpose)


def _purpose(title: str | None, detail: str | None) -> dict[str, Any] | None:
	# Detail without a title is refused as the CLI refuses --purpose-file without --purpose.
	if title is None:
		if detail is not None:
			raise ValueError("purpose_detail needs purpose")
		return None
	return purpose_body(title, detail)


def _built(model: Mapping[str, Any], services: Sequence[str]) -> set[str]:
	# Only the selected services that build need their contexts: up builds nothing else.
	selected = selected_services(model, services)
	return {name for name, service in model["services"].items() if name in selected and service.get("build") is not None}


def _up_query(
	*,
	services: Sequence[str],
	scale: Mapping[str, int] | None,
	build: str,
	recreate: str,
	remove_orphans: bool,
	timeout: int | None,
	config_files: Sequence[str],
	project_directory: str | None,
	dry_run: bool,
) -> Query:
	return (
		Query()
		.each("services", services)
		.each("scale", [f"{service}={count}" for service, count in (scale or {}).items()])
		.add("build", None if build == "missing" else build)
		.add("recreate", None if recreate == "changed" else recreate)
		.flag("removeOrphans", remove_orphans)
		.add("timeout", timeout)
		.flag("dryRun", dry_run)
		.each("configFiles", config_files)
		.add("projectDirectory", project_directory or None)
	)


def _name(model: Mapping[str, Any]) -> str:
	name = model.get("name")
	if not isinstance(name, str) or not name:
		raise ValueError("The model has no name; give the resolved model, which names its project, or set its 'name'.")
	return name


def _has_failed(machine: MachineSummary) -> bool:
	"""Whether a machine ends a wait in failure: it exited, or its health check made it unhealthy."""
	return machine.health == MachineHealth.UNHEALTHY or machine.state == MachineState.EXITED


def _is_up(machine: MachineSummary) -> bool:
	"""Whether a machine is what a wait waits for: running and ready, and healthy when it has a health check."""
	return machine.state == MachineState.RUNNING and machine.ready and machine.health in (MachineHealth.NONE, MachineHealth.HEALTHY)


def _path(fleet: str) -> str:
	return f"{_FLEETS}/{segment(fleet)}"
