"""Named volumes: disks that a fleet declares, which keep their files when the machines that use them are recreated."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from . import _wire
from ._connection import Connection
from ._query import Query, segment

_VOLUMES = "/v1/volumes"


@dataclass(frozen=True, kw_only=True, slots=True)
class VolumeDetail:
	"""One named volume."""

	name: str
	mountpoint: str
	"""The volume's disk file on the host."""

	labels: dict[str, str]
	size_bytes: int
	"""The most bytes the disk may grow to."""

	fleet: str
	created: datetime
	driver: str = "local"
	used_bytes: int | None = None
	"""The size of the disk's file now, or None when the file cannot be read."""

	attached_to: str | None = None
	"""The machine the volume is attached to, or None."""


class VolumesClient:
	"""The named volume routes: list, inspect and remove."""

	def __init__(self, connection: Connection) -> None:
		self._connection = connection

	def list(self, *, filters: Mapping[str, str | Sequence[str]] | None = None) -> list[VolumeDetail]:
		"""Lists the named volumes: ``endia volume ls``."""
		return _wire.read(self._connection.read(_VOLUMES + str(Query().filters(filters))), list[VolumeDetail])

	def inspect(self, volume: str) -> VolumeDetail:
		"""Reads one named volume: ``endia volume inspect``."""
		return _wire.read(self._connection.read(_path(volume)), VolumeDetail)

	def remove(self, volume: str, *, force: bool = False) -> None:
		"""Deletes a named volume's disk and record: ``endia volume rm``. A volume attached to a machine is refused with
		``RESOURCE_IN_USE``.

		Args:
			force: Count a volume that does not exist as removed, instead of refusing it with ``NOT_FOUND``.
		"""
		self._connection.change("DELETE", _path(volume) + str(Query().flag("force", force)))


def _path(volume: str) -> str:
	return f"{_VOLUMES}/{segment(volume)}"
