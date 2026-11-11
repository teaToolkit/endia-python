"""Bases: images with no parent, imported from a disk on the host or prepared from a Windows installation disc."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ._connection import Connection
from .operations import OperationHandle, OperationsClient, handle_of


@dataclass(frozen=True, kw_only=True, slots=True)
class BaseCheckReport:
	"""One check that an imported disk must pass before it becomes an image."""

	name: str
	passed: bool
	detail: str


@dataclass(frozen=True, kw_only=True, slots=True)
class BaseImportResult:
	"""The image an import or a preparation made."""

	digest: str
	reference: str
	disk_path: str
	brought_in: str
	checks: list[BaseCheckReport]


class BasesClient:
	"""The base routes: import and prepare."""

	def __init__(self, connection: Connection, operations: OperationsClient) -> None:
		self._connection = connection
		self._operations = operations

	def import_disk(
		self, path: str, reference: str, *, move: bool = False, changes: Sequence[str] = ()
	) -> OperationHandle[BaseImportResult]:
		"""Starts importing a disk file on the host as an image with no parent: ``endia import``.

		Args:
			path: The disk file's path on the host.
			reference: The tag of the new image.
			move: Move the file into the image folder instead of copying it.
			changes: Recipe instructions applied to the image's configuration, such as ``ENV LANG=en-GB``.
		"""
		body = {"path": path, "reference": reference, "move": move, "changes": list(changes)}
		return handle_of(self._operations, self._connection.change("POST", "/v1/bases/import", json_body=body), BaseImportResult)

	def prepare(
		self,
		iso: str,
		tag: str,
		password: str,
		*,
		image_index: int = 1,
		desktop_user: str = "testuser",
		admin_user: str = "testadmin",
	) -> OperationHandle[BaseImportResult]:
		"""Starts preparing a base from a Windows installation disc on the host: ``endia base prepare``.

		The request carries a password, so it is sent once, with no ``Idempotency-Key``, no repeat after a failed connection and no
		time limit of the client's own.

		Args:
			iso: The disc image's path on the host.
			tag: The tag of the new image.
			password: The password of both accounts the base is given.
			image_index: Which Windows edition on the disc to install, counting from 1.
			desktop_user: The standard account that signs in to the desktop.
			admin_user: The administrator account.
		"""
		body = {
			"iso": iso,
			"tag": tag,
			"imageIndex": image_index,
			"desktopUser": desktop_user,
			"adminUser": admin_user,
			"password": password,
		}
		return handle_of(self._operations, self._connection.once("/v1/bases/prepare", body), BaseImportResult)
