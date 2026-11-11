"""Copying files into and out of a running machine, as ``docker cp`` copies them, through tar archives.

A path in a machine is an absolute Windows path written with ``\\`` or ``/``, such as ``C:\\data`` or ``C:/data``, or a path
relative to ``C:\\``. ``copy_in`` and ``copy_out`` apply Docker's rules for where a copy lands:

* a file is saved under the destination's name, or copied into the destination when it is an existing folder;
* a folder becomes the destination when that does not exist, or is copied into an existing folder as a subfolder;
* a folder whose path ends in ``/.`` has its contents copied into the destination;
* a destination that ends in a separator must be an existing folder, and a folder is never copied onto a file.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import PureWindowsPath
from types import TracebackType
from typing import IO, Any

import httpx

from . import _wire
from ._connection import Connection
from ._query import Query, segment
from ._tar import pack_path, unpack
from .errors import EastIndiaApiError

_PATH_STAT_HEADER = "X-EastIndia-Path-Stat"
_DEFAULT_ROOT = "C:\\"


class PathMode:
	"""What a path in a machine is."""

	FILE = "file"
	DIRECTORY = "directory"
	LINK = "link"


@dataclass(frozen=True, kw_only=True, slots=True)
class PathStat:
	"""What a path in a machine is: its name, size, kind, time of last change and, for a link, what it points at."""

	name: str
	size: int
	"""A file's size in bytes; 0 for a folder or a link."""

	mode: str
	"""One of ``PathMode``."""

	mtime: datetime
	link_target: str | None = None


class MachineArchive:
	"""A path in a machine read as a tar archive: what the path is, and the archive as the service sends it.

	Iterating gives the archive's bytes as they arrive. Closing it closes the answer that carries them.
	"""

	def __init__(self, stat: PathStat, response: httpx.Response) -> None:
		self.stat = stat
		"""What the path is."""
		self._response = response

	def __iter__(self) -> Iterator[bytes]:
		return self._response.iter_bytes()

	def close(self) -> None:
		"""Closes the answer."""
		self._response.close()

	def __enter__(self) -> MachineArchive:
		return self

	def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None) -> None:
		self.close()


class ArchivesClient:
	"""The archive routes: describe a path in a machine, read it as a tar archive, extract a tar archive into a folder of it, and
	delete a path; and ``copy_in`` and ``copy_out``, which copy a local file or folder by Docker's rules.
	"""

	def __init__(self, connection: Connection) -> None:
		self._connection = connection

	def stat(self, machine: str, path: str) -> PathStat | None:
		"""Describes a path in a machine, or gives None when nothing is at it."""
		try:
			headers = self._connection.head(_archive(machine, path))
		except EastIndiaApiError as refused:
			if refused.status_code == httpx.codes.NOT_FOUND and refused.error is not None and refused.error.field_path == "path":
				return None
			raise
		return _stat_of(headers)

	def read(self, machine: str, path: str) -> MachineArchive:
		"""Reads a path in a machine as a tar archive whose root entry is named after the path. A path ending in ``\\.`` reads a
		folder's contents, under a root entry named ``.``.
		"""
		response = self._connection.open_stream(_archive(machine, path))
		try:
			return MachineArchive(_stat_of(response.headers), response)
		except BaseException:
			response.close()
			raise

	def write(self, machine: str, folder: str, archive: IO[bytes]) -> None:
		"""Extracts a tar archive into an existing folder of a machine.

		Args:
			archive: The archive, read from its current position. It must be seekable, so that it can be sent again from there when
				the connection fails before any reply.

		Raises:
			EastIndiaApiError: ``FILES_LOCKED`` when a file to be replaced is in use; the files written before it stay written.
		"""
		self._connection.change("PUT", _archive(machine, folder), archive=archive, timed=False)

	def delete(self, machine: str, path: str) -> None:
		"""Deletes a file, or a folder with everything in it, in a machine."""
		self._connection.change("DELETE", _archive(machine, path))

	def copy_in(self, machine: str, source: str | os.PathLike[str], destination: str) -> None:
		"""Copies a local file or folder into a machine by Docker's rules: ``endia cp SRC MACHINE:DEST``.

		Raises:
			FileNotFoundError: The source does not exist, or the destination ends in a separator and does not exist.
			NotADirectoryError: The source is a file but its path ends in a separator; or the destination ends in a separator but
				is a file; or the source is a folder and the destination a file.
		"""
		local = os.fspath(source)
		if not os.path.lexists(local):
			raise FileNotFoundError(f'no such file or directory: "{local}"')
		to = MachinePath.parse(destination)
		existing = self.stat(machine, to.full_path)
		placement = place(
			source_is_folder=os.path.isdir(local) and not os.path.islink(local),
			source_names_folder=_names_folder(local),
			source_contents_only=_ends_with_current_folder(local),
			destination_kind=_kind(existing),
			destination_names_folder=to.names_folder,
			destination_name=to.name,
		)
		root_name = placement.root_name if placement.root_name is not None else os.path.basename(os.path.normpath(os.path.abspath(local)))
		with pack_path(local, root_name) as archive:
			self.write(machine, to.full_path if placement.into_destination else to.parent, archive)

	def copy_out(self, machine: str, source: str, destination: str | os.PathLike[str]) -> None:
		"""Copies a file or folder out of a machine into a local path by Docker's rules: ``endia cp MACHINE:SRC DEST``.

		Raises:
			FileNotFoundError: Nothing is at the source, or the local folder the copy lands in does not exist.
			NotADirectoryError: The copy is refused by Docker's rules, as for ``copy_in``.
		"""
		local = os.fspath(destination)
		frm = MachinePath.parse(source)
		stat = self.stat(machine, frm.full_path)
		if stat is None:
			raise FileNotFoundError(f'no such file or directory in {machine}: "{frm.full_path}"')
		placement = place(
			source_is_folder=stat.mode == PathMode.DIRECTORY,
			source_names_folder=frm.names_folder,
			source_contents_only=frm.ends_with_current_folder,
			destination_kind=_local_kind(local),
			destination_names_folder=_names_folder(local),
			destination_name=os.path.basename(os.path.normpath(os.path.abspath(local))),
		)
		full = os.path.abspath(local)
		folder = full if placement.into_destination else os.path.dirname(os.path.normpath(full))
		if not os.path.isdir(folder):
			raise FileNotFoundError(f'no such directory: the folder "{folder}" does not exist')
		with self.read(machine, frm.archive_path) as archive:
			unpack(io.BufferedReader(_PieceReader(iter(archive))), folder, placement.root_name)


@dataclass(frozen=True, slots=True)
class Placement:
	"""Where a copy's archive is extracted, as Docker's rules decide it."""

	into_destination: bool
	"""Whether the archive is extracted into the destination, an existing folder; otherwise into the folder that holds it."""

	root_name: str | None
	"""What the archive's root entry is renamed to: a new name; empty to extract the root's contents without the root; None to
	keep the source's name."""


def place(
	*,
	source_is_folder: bool,
	source_names_folder: bool,
	source_contents_only: bool,
	destination_kind: str | None,
	destination_names_folder: bool,
	destination_name: str,
) -> Placement:
	"""Decides where a copy lands by Docker's rules.

	Args:
		source_is_folder: Whether the source is a folder; a file or a link counts as a file.
		source_names_folder: Whether the source path ends in a separator or in ``/.``.
		source_contents_only: Whether the source path ends in ``/.``, which for a folder means its contents.
		destination_kind: ``PathMode.FILE`` or ``PathMode.DIRECTORY`` for what the destination is now; None when it does not exist.
		destination_names_folder: Whether the destination path ends in a separator or in ``/.``.
		destination_name: The destination path's last name.

	Raises:
		NotADirectoryError: A file whose path says it is a folder; a destination whose path says it is a folder but that is a
			file; or a folder copied onto a file.
		FileNotFoundError: A file copied to a destination whose path says it is a folder, which does not exist.
	"""
	if not source_is_folder:
		if source_names_folder:
			raise NotADirectoryError("not a directory: the source is a file, but its path ends in a separator or in /.")
		if destination_kind is None and destination_names_folder:
			raise FileNotFoundError(f'no such directory: the folder "{destination_name}" must exist to copy a file into it')
		if destination_kind == PathMode.FILE and destination_names_folder:
			raise NotADirectoryError(f'no such directory: "{destination_name}" is a file, but its path ends in a separator or in /.')
		if destination_kind == PathMode.DIRECTORY:
			return Placement(into_destination=True, root_name=None)
		return Placement(into_destination=False, root_name=destination_name)
	if destination_kind == PathMode.FILE:
		raise NotADirectoryError(f'cannot copy a directory to a file: "{destination_name}" is a file')
	if destination_kind is None:
		return Placement(into_destination=False, root_name=destination_name)
	return Placement(into_destination=True, root_name="" if source_contents_only else None)


@dataclass(frozen=True, slots=True)
class MachinePath:
	"""A path in a machine, read as the archive routes read it."""

	full_path: str
	"""The absolute path with ``\\`` separators and no trailing one, except for a drive's root such as ``C:\\``."""

	ends_with_separator: bool
	ends_with_current_folder: bool
	"""Whether the text ended in ``/.``, which for a folder means its contents."""

	@classmethod
	def parse(cls, text: str) -> MachinePath:
		"""Reads a path; a relative path is taken from ``C:\\``, and ``..`` never climbs above the drive's root."""
		slashed = text.replace("/", "\\")
		ends_with_current_folder = slashed == "." or slashed.endswith("\\.")
		ends_with_separator = slashed.endswith("\\")
		windows = PureWindowsPath(slashed)
		absolute = windows if windows.drive and windows.root else PureWindowsPath(_DEFAULT_ROOT, *windows.parts)
		names: list[str] = []
		for part in absolute.parts[1:]:
			if part == "..":
				if names:
					names.pop()
			elif part != ".":
				names.append(part)
		full = str(PureWindowsPath(absolute.anchor.upper(), *names))
		return cls(full, ends_with_separator, ends_with_current_folder)

	@property
	def names_folder(self) -> bool:
		"""Whether the text ended in a separator or in ``/.``, either of which Docker's rules read as saying it is a folder."""
		return self.ends_with_separator or self.ends_with_current_folder

	@property
	def name(self) -> str:
		"""The path's last name; empty for a drive's root."""
		return PureWindowsPath(self.full_path).name

	@property
	def parent(self) -> str:
		"""The folder that holds the path; a drive's root for a drive's root."""
		return str(PureWindowsPath(self.full_path).parent)

	@property
	def archive_path(self) -> str:
		"""The path a copy out of the machine asks for: followed by ``\\.`` when the text ended in ``/.``, so that the service writes
		the folder's contents.
		"""
		return self.full_path.rstrip("\\") + "\\." if self.ends_with_current_folder else self.full_path


class _PieceReader(io.RawIOBase):
	"""A readable stream over the pieces of an answer, for the tar reader."""

	def __init__(self, pieces: Iterator[bytes]) -> None:
		self._pieces = pieces
		self._piece = b""

	def readable(self) -> bool:
		return True

	def readinto(self, buffer: Any) -> int:
		while not self._piece:
			piece = next(self._pieces, None)
			if piece is None:
				return 0
			self._piece = piece
		count = min(len(buffer), len(self._piece))
		buffer[:count] = self._piece[:count]
		self._piece = self._piece[count:]
		return count


def _archive(machine: str, path: str) -> str:
	return f"/v1/machines/{segment(machine)}/archive" + str(Query().add("path", path))


def _stat_of(headers: httpx.Headers) -> PathStat:
	try:
		return _wire.read(json.loads(base64.b64decode(headers[_PATH_STAT_HEADER], validate=True)), PathStat)
	except (KeyError, binascii.Error) as missing:
		raise ValueError(f"The service answered without a readable {_PATH_STAT_HEADER} header.") from missing


def _kind(stat: PathStat | None) -> str | None:
	if stat is None:
		return None
	return PathMode.DIRECTORY if stat.mode == PathMode.DIRECTORY else PathMode.FILE


def _local_kind(path: str) -> str | None:
	if not os.path.lexists(path):
		return None
	return PathMode.DIRECTORY if os.path.isdir(path) else PathMode.FILE


def _names_folder(path: str) -> bool:
	return path.endswith(("/", os.sep)) or _ends_with_current_folder(path)


def _ends_with_current_folder(path: str) -> bool:
	return path == "." or path.endswith(("/.", os.sep + "."))
