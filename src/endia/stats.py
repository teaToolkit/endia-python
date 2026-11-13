"""Readings: what a machine, or the host, uses of processors, memory, network and disk, one reading at a time or as a stream."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime

from . import _wire
from ._connection import Connection
from ._query import Query, segment


@dataclass(frozen=True, kw_only=True, slots=True)
class UnavailableReading:
	"""A reading that could not be taken, and why."""

	reading: str
	reason: str


@dataclass(frozen=True, kw_only=True, slots=True)
class MachineStats:
	"""One reading of a machine. A value that could not be read is None, and ``unavailable`` says why."""

	read: datetime
	id: str
	name: str
	state: str
	processors: int
	memory_limit_bytes: int
	unavailable: list[UnavailableReading]
	cpu_percent: float | None = None
	memory_usage_bytes: int | None = None
	network_received_bytes: int | None = None
	network_sent_bytes: int | None = None
	disk_read_bytes: int | None = None
	disk_written_bytes: int | None = None
	processes: int | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class HostStats:
	"""One reading of the host. A value that could not be read is None, and ``unavailable`` says why."""

	read: datetime
	logical_processors: int
	memory_reserve_bytes: int
	machines_memory_bytes: int
	unavailable: list[UnavailableReading]
	cpu_percent: float | None = None
	memory_total_bytes: int | None = None
	memory_available_bytes: int | None = None
	disk_bytes_per_second: float | None = None
	"""The bytes all the host's physical disks read and wrote each second, together, over the last interval of readings."""

	network_bytes_per_second: float | None = None
	"""The bytes all the host's physical network adapters received and sent each second, together, over the last interval of readings."""


class StatsClient:
	"""The readings routes: ``endia stats`` for a machine and, with ``--host``, for the host."""

	def __init__(self, connection: Connection) -> None:
		self._connection = connection

	def machine(self, machine: str) -> MachineStats:
		"""Takes one reading of a machine."""
		return _first(self._read(_machine(machine), stream=False, cls=MachineStats))

	def stream_machine(self, machine: str) -> Iterator[MachineStats]:
		"""Reads a machine once each interval the host configures, until the iteration is abandoned."""
		return self._read(_machine(machine), stream=True, cls=MachineStats)

	def host(self) -> HostStats:
		"""Takes one reading of the host."""
		return _first(self._read("/v1/host/stats", stream=False, cls=HostStats))

	def stream_host(self) -> Iterator[HostStats]:
		"""Reads the host once each interval the host configures, until the iteration is abandoned."""
		return self._read("/v1/host/stats", stream=True, cls=HostStats)

	def _read(self, path: str, *, stream: bool, cls: type[_wire.T]) -> Generator[_wire.T, None, None]:
		# The route streams unless told not to, as Docker's does, so false is sent rather than left out.
		for line in self._connection.lines(path + str(Query().explicit("stream", stream))):
			if line:
				yield _wire.read_json_line(line, cls)


def _first(readings: Generator[_wire.T, None, None]) -> _wire.T:
	with closing(readings):
		return next(readings)


def _machine(machine: str) -> str:
	return f"/v1/machines/{segment(machine)}/stats"
