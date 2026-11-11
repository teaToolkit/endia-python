"""Images: the disks that machines are made from, kept in layers, each image a child of the one it was built on."""

from __future__ import annotations

import builtins
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import IO

from . import _wire
from ._connection import Connection
from ._query import Query
from ._query import reference as image_path
from ._tar import default_recipe, pack_build_context
from .operations import OperationHandle, OperationsClient, handle_of

_IMAGES = "/v1/images"


@dataclass(frozen=True, kw_only=True, slots=True)
class ImageSummary:
	"""One image as ``endia images`` lists it."""

	id: str
	"""The image's digest, ``sha256:`` and 64 hexadecimal digits."""

	repo_tags: list[str]
	created: datetime
	size: int
	labels: dict[str, str]
	expiry_warning: bool
	"""Whether the Windows evaluation licence of the image's base expires soon."""

	machines: int
	"""How many machines use the image."""

	children: int
	"""How many images were built on the image."""

	parent_id: str | None = None
	evaluation_expires: datetime | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ImageConfigDetail:
	"""What a machine made from the image runs with, unless its service says otherwise."""

	env: list[str]
	working_dir: str
	user: str
	exposed_ports: list[str]
	shell: list[str]
	cmd: list[str] | None = None
	entrypoint: list[str] | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ImageDiskDetail:
	"""The image's disk file on the host."""

	path: str
	brought_in: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ImageBuildDetail:
	"""The recipe and build arguments an image was built from."""

	recipe: str
	build_args: dict[str, str]


@dataclass(frozen=True, kw_only=True, slots=True)
class ImageDetail:
	"""One image in full: ``endia image inspect``."""

	id: str
	repo_tags: list[str]
	created: datetime
	os: str
	architecture: str
	size: int
	labels: dict[str, str]
	chain: list[str]
	"""The digests of the image's parents, nearest first."""

	disk: ImageDiskDetail
	machines: list[str]
	children: list[str]
	parent_id: str | None = None
	config: ImageConfigDetail | None = None
	build_key: str | None = None
	"""The key that identifies the build: an identical build reuses the image."""

	build: ImageBuildDetail | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class TagResult:
	"""The tag that was set, and the image it pointed at before."""

	target: str
	digest: str
	previous_digest: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class RemovalEntry:
	"""One step of a removal: a tag taken off, or an image deleted."""

	untagged: str | None = None
	deleted: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class BuildStageResult:
	"""What one stage of a build gave: the image, for the target stage, or a build-cache entry, for a stage below it.

	A stage is the part of a recipe from one ``FROM`` to the next. A build-cache entry is a stage's committed result that is not an
	image: no listing of images shows it, but a later build whose stage has the same build key reuses it.
	"""

	digest: str
	"""The digest of the stage's result, ``sha256:`` and 64 hexadecimal digits."""

	build_key: str
	"""The stage's build key: a SHA-256 hash of everything that decides the stage's result."""

	cache_hit: bool
	"""Whether an image or build-cache entry with the stage's build key already existed, so that the stage was not built."""

	name: str | None = None
	"""The name that the stage's ``FROM ... AS`` gives it, in lower case, or None for a stage without one."""


@dataclass(frozen=True, kw_only=True, slots=True)
class BuildResult:
	"""The image a build made for its target stage, or the identical image it reused."""

	digest: str
	tags: list[str]
	build_key: str
	"""The target stage's build key."""

	cache_hit: bool
	"""Whether every stage was found in the cache, so that nothing was built."""

	seconds: float
	stages: list[BuildStageResult] = field(default_factory=list)
	"""Each stage the build made or found, from the lowest, built on an image, to the target. A recipe of one stage gives one."""


@dataclass(frozen=True, kw_only=True, slots=True)
class BuilderPruneResult:
	"""What ``endia builder prune`` deleted."""

	deleted_build_cache: list[str]
	"""The build-cache entries deleted, by digest, in the order deleted."""

	reclaimed_bytes: int


class ImagesClient:
	"""The image routes: build, list, inspect, tag and remove, and the prune of the build cache that builds leave."""

	def __init__(self, connection: Connection, operations: OperationsClient) -> None:
		self._connection = connection
		self._operations = operations

	def list(self, *, filters: Mapping[str, str | Sequence[str]] | None = None, reference: str | None = None) -> list[ImageSummary]:
		"""Lists the images: ``endia images``.

		Args:
			filters: Docker-style filters, such as ``{"dangling": "true"}`` or ``{"label": ["team=lab"]}``.
			reference: Lists only the images with a tag that matches, each with only those tags.
		"""
		query = Query().filters(filters).add("reference", reference)
		return _wire.read(self._connection.read(_IMAGES + str(query)), list[ImageSummary])

	def inspect(self, image: str) -> ImageDetail:
		"""Reads one image in full, named by a tag, a digest or a unique prefix of its digest."""
		return _wire.read(self._connection.read(f"{_IMAGES}/{image_path(image)}"), ImageDetail)

	def tag(self, source: str, target: str) -> TagResult:
		"""Points the tag ``target`` at the image ``source`` names: ``endia tag``."""
		answer = self._connection.change("PUT", f"{_IMAGES}/tags/{image_path(target)}", json_body={"source": source})
		return _wire.read(answer.json(), TagResult)

	def remove(self, image: str, *, force: bool = False) -> builtins.list[RemovalEntry]:
		"""Removes a tag, or an image with no tags left: ``endia rmi``.

		Args:
			force: Remove an image named by its digest even though it has several tags.
		"""
		answer = self._connection.change("DELETE", f"{_IMAGES}/{image_path(image)}" + str(Query().flag("force", force)))
		return _wire.read(answer.json(), list[RemovalEntry])

	def build(
		self,
		context: str | os.PathLike[str],
		*,
		dockerfile: str | os.PathLike[str] | None = None,
		tags: Sequence[str] = (),
		build_args: Mapping[str, str] | None = None,
		labels: Mapping[str, str] | None = None,
		target: str | None = None,
		no_cache: bool = False,
		memory: int | None = None,
		cpus: float | None = None,
	) -> OperationHandle[BuildResult]:
		"""Starts a build from a context folder, packed as ``endia build`` packs it.

		Every file, folder and link of the folder is sent except those the ignore file leaves out. The ignore file is the first that
		exists of the recipe's own ``.endiaignore`` and ``.dockerignore``, each named as the recipe followed by that name, and then
		``.endiaignore`` and ``.dockerignore`` at the folder's root.

		A recipe of several stages, each beginning with a ``FROM``, builds its last stage unless ``target`` names another. For the
		recipe::

			FROM eastindia/development:latest AS tools
			RUN choco install -y git

			FROM tools AS app
			COPY publish/ C:/app/

		``client.images.build("lab-image", target="tools", tags=["lab/tools:1"]).result()`` builds and tags the first stage alone.
		A later build without ``target`` finds that stage in the cache and builds only ``app``.

		Args:
			context: The context folder.
			dockerfile: The recipe, relative to the context folder or absolute; it may lie outside the folder. Default:
				``Endiafile`` in the folder when it exists, and ``Dockerfile`` otherwise.
			tags: Tags for the new image, such as ``lab/dotnet:10``.
			build_args: Values for the recipe's ``ARG`` instructions.
			labels: Labels for the new image.
			target: The recipe's stage to build, named by its ``FROM ... AS``, as ``endia build --target`` names it; the tags name
				that stage's result. Default: the last stage. The stages it is built on are built too, or found in the cache.
			no_cache: Build every stage even if an identical build exists.
			memory: The builder's memory in bytes; less than 2 GiB is refused.
			cpus: The builder's processors, as ``docker run --cpus`` counts them.

		Raises:
			NotADirectoryError: The context is not a folder.
			FileNotFoundError: The recipe does not exist.
			IgnoreFileError: The ignore file holds a pattern that is not valid.
		"""
		folder = os.fspath(context)
		recipe = os.path.join(folder, os.fspath(dockerfile) if dockerfile is not None else default_recipe(folder))
		archive, recipe_name = pack_build_context(folder, recipe)
		with archive:
			return self.build_archive(
				archive,
				dockerfile=recipe_name,
				tags=tags,
				build_args=build_args,
				labels=labels,
				target=target,
				no_cache=no_cache,
				memory=memory,
				cpus=cpus,
			)

	def build_archive(
		self,
		archive: IO[bytes],
		*,
		dockerfile: str | None = None,
		tags: Sequence[str] = (),
		build_args: Mapping[str, str] | None = None,
		labels: Mapping[str, str] | None = None,
		target: str | None = None,
		no_cache: bool = False,
		memory: int | None = None,
		cpus: float | None = None,
	) -> OperationHandle[BuildResult]:
		"""Starts a build from a context the caller has already packed as a tar archive.

		Args:
			archive: The context, read from its current position. It must be seekable, so that it can be sent again from there
				when the connection fails before any reply.
			dockerfile: The recipe's path inside the archive, written with ``/``. Default: the service reads ``Endiafile`` at the
				archive's root when it holds one, and ``Dockerfile`` otherwise.

		The other arguments are those of ``build``.
		"""
		query = (
			Query()
			.each("t", tags)
			.add("dockerfile", dockerfile)
			.json("buildargs", build_args)
			.json("labels", labels)
			.add("target", target)
			.add("memory", memory)
			.add("cpus", cpus)
			.flag("nocache", no_cache)
		)
		answer = self._connection.change("POST", f"{_IMAGES}/build" + str(query), archive=archive, timed=False)
		return handle_of(self._operations, answer, BuildResult)

	def prune_build_cache(self, *, all: bool = False) -> OperationHandle[BuilderPruneResult]:
		"""Starts deleting the unused build cache: ``endia builder prune``.

		A build-cache entry is unused when no image, machine or other entry is built on it and no running build has made or found
		it. The prune deletes every unused entry, repeating until none remains, and leaves the entries that running builds use.

		Args:
			all: Docker's ``--all``. It deletes the same entries, because once a build has finished the build cache holds nothing
				that a build still uses.
		"""
		query = Query().flag("all", all)
		return handle_of(self._operations, self._connection.change("POST", "/v1/builder/prune" + str(query)), BuilderPruneResult)
