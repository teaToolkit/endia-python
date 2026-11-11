"""The body of ``compose up``: one tar archive holding what the service needs from the caller's side of a fleet.

A caller in a sandbox shares no folder with the host, so the archive carries the files themselves. It holds ``compose.json``,
the resolved model; ``contexts/<service>/``, the build context of each selected service that builds, holding the files
``endia build`` would send; and ``binds/<service>/<index>``, the source of each bind mount of every service, by the index of
its entry in the service's ``volumes``, counted from 0. When the caller sets the fleet's purpose as it comes up, it also holds
``purpose.json``, the purpose's title and detail.

The SDK does not load Compose files. It reads the resolved model that ``endia compose config --format json`` prints: every
path absolute, and every build and volume in long syntax. A model written by hand works when it follows the same form.
"""

import io
import json
import os
import tarfile
import tempfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import IO, Any

from ._tar import add_entry, context_entries, default_recipe, ignore_file_for, path_inside

MODEL_ENTRY = "compose.json"
PURPOSE_ENTRY = "purpose.json"
_CONTEXTS = "contexts"
_BINDS = "binds"
_BIND_TYPE = "bind"
_FORM = "as 'endia compose config --format json' prints it"


def selected_services(model: Mapping[str, Any], named: Sequence[str]) -> set[str]:
	"""Gives the services ``compose up`` acts on: every service when none is named, else those named and every service they
	depend on, directly or through others.

	Raises:
		ValueError: A named service is not in the model.
	"""
	services = _services(model)
	unknown = [name for name in named if name not in services]
	if unknown:
		raise ValueError(f"no such service: {unknown[0]}")
	if not named:
		return set(services)
	selected: set[str] = set()
	waiting = list(named)
	while waiting:
		name = waiting.pop()
		if name not in selected:
			selected.add(name)
			waiting.extend(_dependencies(services.get(name, {})))
	return selected


def pack_fleet(model: Mapping[str, Any], built: Iterable[str], purpose: Mapping[str, Any] | None = None) -> IO[bytes]:
	"""Writes the archive of a resolved model, with the build contexts of the services in ``built``.

	A ``purpose``, the JSON of ``purposes.purpose_body``, becomes ``purpose.json``, which the service stores as the fleet's
	purpose before any other work. None sends none, and a purpose the fleet already has stays.

	Raises:
		ValueError: The model is not in resolved form: a build or a volume in short syntax, or a path that is not absolute.
		NotADirectoryError: A build context is not a folder.
		FileNotFoundError: A bind mount's source does not exist.
	"""
	entries = list(_entries(_services(model), set(built)))
	archive = tempfile.TemporaryFile()  # noqa: SIM115 - the caller closes the archive once it has been sent.
	with tarfile.open(fileobj=archive, mode="w", format=tarfile.PAX_FORMAT) as tar:
		text = json.dumps(model, indent=2).encode()
		info = tarfile.TarInfo(MODEL_ENTRY)
		info.size = len(text)
		tar.addfile(info, io.BytesIO(text))
		if purpose is not None:
			note = json.dumps(purpose).encode()
			entry = tarfile.TarInfo(PURPOSE_ENTRY)
			entry.size = len(note)
			tar.addfile(entry, io.BytesIO(note))
		for name, path in entries:
			if path is None:
				folder = tarfile.TarInfo(name)
				folder.type = tarfile.DIRTYPE
				folder.mode = 0o755
				tar.addfile(folder)
			else:
				# The service accepts only files and folders, so a link is sent as what it points at.
				add_entry(tar, name, path, follow_links=True)
	archive.seek(0)
	return archive


def _entries(services: Mapping[str, Any], built: set[str]) -> Iterator[tuple[str, str | None]]:
	# Every entry after compose.json: its name in the archive and the path it is read from, or None for a folder.
	for name, service in services.items():
		build = service.get("build")
		if build is not None and name in built:
			yield from _context_entries(name, build)
		for index, volume in enumerate(service.get("volumes") or []):
			if not isinstance(volume, Mapping):
				raise ValueError(f'service "{name}" has volume {index} in short syntax; give the model in long syntax, {_FORM}')
			if volume.get("type") == _BIND_TYPE and volume.get("source"):
				yield from _bind_entries(name, index, _absolute(name, "bind mount source", volume["source"]))


def _context_entries(service: str, build: Any) -> Iterator[tuple[str, str | None]]:
	if not isinstance(build, Mapping) or not build.get("context"):
		raise ValueError(f'service "{service}" has a build without a context in long syntax; give the model {_FORM}')
	folder = _absolute(service, "build context", build["context"])
	if not os.path.isdir(folder):
		raise NotADirectoryError(f"the build context '{folder}' of service \"{service}\" is not a folder")
	recipe = os.path.join(folder, build.get("dockerfile") or default_recipe(folder))
	inside = path_inside(folder, recipe)
	if inside is None:
		raise ValueError(f"the recipe '{recipe}' of service \"{service}\" must be inside its build context '{folder}'")
	root = f"{_CONTEXTS}/{service}"
	yield root + "/", None
	for relative, path in context_entries(folder, inside, ignore_file_for(folder, recipe)):
		# A folder of the context gets no entry of its own: extracting a file beneath it creates the folder.
		if not os.path.isdir(path) or os.path.islink(path):
			yield f"{root}/{relative}", path


def _bind_entries(service: str, index: int, source: str) -> Iterator[tuple[str, str | None]]:
	root = f"{_BINDS}/{service}/{index}"
	if os.path.isfile(source):
		yield root, source
		return
	if not os.path.isdir(source):
		raise FileNotFoundError(f"the bind mount source '{source}' of service \"{service}\" does not exist")
	yield root + "/", None
	for folder, children, files in os.walk(source, followlinks=True):
		children.sort()
		relative = os.path.relpath(folder, source).replace(os.sep, "/")
		prefix = root + "/" if relative == "." else f"{root}/{relative}/"
		for child in children:
			yield f"{prefix}{child}/", None
		for file in sorted(files):
			yield prefix + file, os.path.join(folder, file)


def _services(model: Mapping[str, Any]) -> Mapping[str, Any]:
	services = model.get("services")
	if not isinstance(services, Mapping):
		raise ValueError(f"The model has no services; give the model {_FORM}.")
	return services


def _dependencies(service: Mapping[str, Any]) -> list[str]:
	# A service's depends_on names the services it depends on. The resolved model writes it as a mapping whose keys are those
	# services; the short syntax writes it as a list of their names. Either form gives the names when it is listed.
	depends_on = service.get("depends_on") or {}
	return list(depends_on)


def _absolute(service: str, what: str, path: str) -> str:
	if not os.path.isabs(path):
		raise ValueError(f"the {what} '{path}' of service \"{service}\" is not an absolute path; give the model {_FORM}")
	return path
