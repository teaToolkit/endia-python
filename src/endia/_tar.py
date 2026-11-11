"""The tar archives the SDK writes and reads: a build's context, the body of ``compose up``, and the archives of a copy.

Every archive is written to a temporary file, so that a large context never sits in memory and an upload can be sent again from
its start when its connection fails. The caller closes the file, which deletes it.
"""

import os
import secrets
import shutil
import tarfile
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import IO

from ._dockerignore import ENDIA_IGNORE_FILE_NAME, IGNORE_FILE_NAME, DockerIgnore

# A build that names no recipe reads EastIndia's name for one, Endiafile, when the context holds it, and Docker's otherwise.
_ENDIAFILE = "Endiafile"
_DOCKERFILE = "Dockerfile"

# A recipe outside the context folder is added at the archive's root. Its name there is ".dockerfile." followed by 16 random
# hexadecimal digits, the name docker build gives such a recipe.
_OUTSIDE_RECIPE_PREFIX = ".dockerfile."
_OUTSIDE_RECIPE_BYTES = 8


def path_inside(folder: str, path: str) -> str | None:
	"""Gives a path's place inside a folder, written with ``/``, or None when the path lies outside the folder."""
	relative = os.path.relpath(os.path.abspath(path), os.path.abspath(folder)).replace(os.sep, "/")
	if relative == ".." or relative.startswith("../") or os.path.isabs(relative):
		return None
	return relative


def default_recipe(folder: str) -> str:
	"""Gives the recipe of a build that names none, as the ``endia`` CLI chooses it: ``Endiafile`` at the root of the context folder
	when it exists, and ``Dockerfile`` otherwise.
	"""
	return _ENDIAFILE if os.path.isfile(os.path.join(folder, _ENDIAFILE)) else _DOCKERFILE


def ignore_file_for(folder: str, recipe: str) -> str:
	"""Gives the ignore file a build uses, as the ``endia`` CLI chooses it: the first that exists of the recipe's own
	``.endiaignore`` and ``.dockerignore``, each named as the recipe followed by that name, and then ``.endiaignore`` and
	``.dockerignore`` at the root of the context folder. When none exists it gives the root ``.dockerignore``, which does not exist.
	"""
	candidates = (
		recipe + ENDIA_IGNORE_FILE_NAME,
		recipe + IGNORE_FILE_NAME,
		os.path.join(folder, ENDIA_IGNORE_FILE_NAME),
		os.path.join(folder, IGNORE_FILE_NAME),
	)
	return next((candidate for candidate in candidates if os.path.isfile(candidate)), candidates[-1])


def context_entries(folder: str, recipe_inside: str | None, ignore_file: str) -> Iterator[tuple[str, str]]:
	"""Gives every file, folder and link of a context folder that a build sends, each as its name in the archive and its path.

	The ignore file decides what is left out, but the recipe and the ignore file are always sent. A folder that is left out is
	still walked when the recipe or the ignore file lies beneath it. A link is sent as a link and never walked. Names are listed in
	ordinal order within each folder.

	Raises:
		IgnoreFileError: The ignore file holds a pattern that is not valid.
	"""
	ignore = (
		DockerIgnore.parse(Path(ignore_file).read_text(encoding="utf-8-sig"), ignore_file)
		if os.path.isfile(ignore_file)
		else DockerIgnore()
	)
	always_sent = {name for name in (recipe_inside, path_inside(folder, ignore_file)) if name is not None}
	yield from _entries_under(folder, "", ignore, always_sent)


def _entries_under(folder: str, prefix: str, ignore: DockerIgnore, always_sent: set[str]) -> Iterator[tuple[str, str]]:
	for child in sorted(os.listdir(folder)):
		name = prefix + child
		path = os.path.join(folder, child)
		sent = name in always_sent or not ignore.is_ignored(name)
		if sent:
			yield name, path
		holds_sent = any(kept.startswith(name + "/") for kept in always_sent)
		if os.path.isdir(path) and not os.path.islink(path) and (sent or holds_sent):
			yield from _entries_under(path, name + "/", ignore, always_sent)


def pack_build_context(folder: str, recipe: str) -> tuple[IO[bytes], str]:
	"""Writes a build's context folder as ``endia build`` sends it, and gives the archive with the recipe's name inside it.

	A recipe outside the folder is added at the archive's root under a random name, with its own ignore file beside it when it
	has one.

	Raises:
		NotADirectoryError: The context is not a folder.
		FileNotFoundError: The recipe does not exist.
		IgnoreFileError: The ignore file holds a pattern that is not valid.
	"""
	if not os.path.isdir(folder):
		raise NotADirectoryError(f"The build context '{folder}' is not a folder.")
	inside = path_inside(folder, recipe)
	if not os.path.isfile(recipe):
		raise FileNotFoundError(f"The recipe '{recipe}' does not exist.")
	ignore_file = ignore_file_for(folder, recipe)
	name = inside if inside is not None else _OUTSIDE_RECIPE_PREFIX + secrets.token_hex(_OUTSIDE_RECIPE_BYTES)
	archive = tempfile.TemporaryFile()  # noqa: SIM115 - the caller closes the archive once it has been sent.
	with tarfile.open(fileobj=archive, mode="w", format=tarfile.PAX_FORMAT) as tar:
		for entry_name, path in context_entries(folder, inside, ignore_file):
			add_entry(tar, entry_name, path)
		if inside is None:
			add_entry(tar, name, recipe)
			if path_inside(folder, ignore_file) is None:
				# Only a recipe's own ignore file lies outside the folder; the root one is already in the archive.
				add_entry(tar, name + ignore_file[len(recipe) :], ignore_file)
	archive.seek(0)
	return archive, name


def add_entry(tar: tarfile.TarFile, name: str, path: str, *, follow_links: bool = False) -> None:
	"""Adds one file, folder or link under a name, without what a folder holds. The entry names no local user or group."""
	info = tar.gettarinfo(os.path.realpath(path) if follow_links else path, arcname=name)
	info.uid = info.gid = 0
	info.uname = info.gname = ""
	if info.isfile():
		with open(path, "rb") as content:
			tar.addfile(info, content)
	else:
		tar.addfile(info)


def pack_path(source: str, root_name: str) -> IO[bytes]:
	"""Writes a file or a folder as the archive of a copy: one root entry under ``root_name``, and every file and folder beneath
	it. An empty ``root_name`` writes a folder's contents without the root entry.
	"""
	archive = tempfile.TemporaryFile()  # noqa: SIM115 - the caller closes the archive once it has been sent.
	with tarfile.open(fileobj=archive, mode="w", format=tarfile.PAX_FORMAT) as tar:
		if root_name:
			add_entry(tar, root_name, source)
		if os.path.isdir(source) and not os.path.islink(source):
			for relative, path in _walk(source):
				add_entry(tar, f"{root_name}/{relative}" if root_name else relative, path)
	archive.seek(0)
	return archive


def _walk(folder: str, prefix: str = "") -> Iterator[tuple[str, str]]:
	for child in sorted(os.listdir(folder)):
		path = os.path.join(folder, child)
		yield prefix + child, path
		if os.path.isdir(path) and not os.path.islink(path):
			yield from _walk(path, prefix + child + "/")


def unpack(archive: IO[bytes], folder: str, root_name: str | None) -> None:
	"""Extracts a copy's archive into an existing folder, with the archive's root entry renamed.

	Args:
		root_name: The root entry's new name; empty to extract the root's contents without the root; None to keep its name.

	Raises:
		tarfile.TarError: The body is not a tar archive.
		ValueError: An entry would be written outside the folder, or through a link.
	"""
	target = os.path.abspath(folder)
	root: str | None = None
	with tarfile.open(fileobj=archive, mode="r|") as tar:
		for member in tar:
			names = [name for name in member.name.split("/") if name not in ("", ".")]
			if root is None:
				# An archive of a folder's contents starts with the current folder, which names no root of its own.
				root = names[0] if names else ""
			names = _renamed(names, root, root_name)
			if not names:
				continue
			path = _inside(target, names)
			if member.isdir():
				os.makedirs(path, exist_ok=True)
			elif member.isfile():
				content = tar.extractfile(member)
				if content is not None:
					with open(path, "wb") as written:
						shutil.copyfileobj(content, written)
			elif member.issym():
				if os.path.lexists(path):
					os.remove(path)
				os.symlink(member.linkname, path)


def _renamed(names: list[str], root: str, root_name: str | None) -> list[str]:
	if root_name is None:
		return names
	if root == "":
		return names if root_name == "" else [root_name, *names]
	if not names or names[0] != root:
		return names
	return names[1:] if root_name == "" else [root_name, *names[1:]]


def _inside(target: str, names: list[str]) -> str:
	if ".." in names:
		raise ValueError(f"The archive entry '{'/'.join(names)}' leaves the folder it is extracted into.")
	path = target
	for name in names[:-1]:
		path = os.path.join(path, name)
		if os.path.islink(path):
			raise ValueError(f"The archive entry '{'/'.join(names)}' passes through the link '{path}'.")
	return os.path.join(path, names[-1])
