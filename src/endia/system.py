"""The service as a whole: its version, the host's description, and the disk space its images, machines and volumes hold."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import _wire
from ._connection import Connection
from ._query import Query
from ._wire import wire
from .operations import OperationHandle, OperationsClient, handle_of


@dataclass(frozen=True, kw_only=True, slots=True)
class VersionInfo:
	"""The service's version."""

	version: str
	git_commit: str
	built: datetime
	os: str
	api_version: str = "1"
	arch: str = "amd64"
	guest_service_version: str | None = None
	"""The version of the guest service that images are given as they are prepared."""


@dataclass(frozen=True, kw_only=True, slots=True)
class HyperVStatus:
	"""Whether Hyper-V is ready for EastIndia."""

	ready: bool
	internet_switch: str
	internet_switch_present: bool
	reason: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class AddressPoolView:
	"""One pool of addresses from which fleets' networks take their subnets."""

	base: str
	size: int


@dataclass(frozen=True, kw_only=True, slots=True)
class HostConfigurationView:
	"""The service's configuration in effect, each value as the host's configuration file names it."""

	image_folder: str | None
	listen_address: str
	listen_port: int
	resource_prefix: str
	internet_switch: str
	host_reserve_mib: int = wire("hostReserveMiB")
	min_free_disk_gib: int = wire("minFreeDiskGiB")
	builder_memory_mib: int = wire("builderMemoryMiB")
	builder_processor_count: int
	base_prepare_disk_gib: int = wire("basePrepareDiskGiB")
	base_prepare_timeout_seconds: int
	guest_programs_folder: str | None
	machine_memory_mib: int = wire("machineMemoryMiB")
	machine_memory_floor_mib: int = wire("machineMemoryFloorMiB")
	machine_memory_ceiling_mib: int = wire("machineMemoryCeilingMiB")
	machine_processor_count: int
	max_layer_depth: int
	build_cache_keep_gib: int = wire("buildCacheKeepGiB")
	max_parallel_boots: int
	guest_connect_timeout_seconds: int
	guest_ready_timeout_seconds: int
	desktop_ready_timeout_seconds: int
	setup_timeout_seconds: int
	stop_timeout_seconds: int
	build_step_timeout_seconds: int
	restart_timeout_seconds: int
	desktop_action_timeout_seconds: int
	uac_settle_seconds: int
	restart_delay_initial_milliseconds: int
	restart_delay_max_seconds: int
	restart_reset_after_seconds: int
	default_address_pools: list[AddressPoolView] | None
	volume_size_gib: int = wire("volumeSizeGiB")
	machine_log_max_bytes: int
	stats_interval_seconds: int
	operation_retention_days: int
	idempotency_retention_hours: int
	purpose_history_max_entries: int
	service_log_retention_days: int
	retention_sweep_minutes: int
	max_context_bytes: int
	client_certificate_validity_days: int
	evaluation_warning_days: int
	hyper_v_request_timeout_seconds: int
	hyper_v_job_timeout_seconds: int
	turn_off_timeout_seconds: int
	ssh_ready_timeout_seconds: int
	stats_read_timeout_seconds: int


@dataclass(frozen=True, kw_only=True, slots=True)
class SystemInfo:
	"""The host's description, the configuration in effect, and what needs attention."""

	name: str
	server_version: str
	operating_system: str
	os_version: str
	logical_processors: int
	memory_total_bytes: int
	storage_root: str
	image_folder: str
	machines: int
	machines_running: int
	machines_exited: int
	images: int
	fleets: int
	volumes: int
	hyper_v: HyperVStatus
	config: HostConfigurationView
	warnings: list[str]
	architecture: str = "x86_64"


@dataclass(frozen=True, kw_only=True, slots=True)
class ImageUsage:
	"""What one image holds on disk."""

	id: str
	repo_tags: list[str]
	parent_id: str | None
	created: datetime
	size_bytes: int
	machines: int
	children: int
	reclaimable: bool


@dataclass(frozen=True, kw_only=True, slots=True)
class MachineUsage:
	"""What one machine's own disk holds."""

	name: str
	state: str
	size_bytes: int


@dataclass(frozen=True, kw_only=True, slots=True)
class VolumeUsage:
	"""What one named volume holds."""

	name: str
	size_bytes: int
	attached_to: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class BuildCacheUsage:
	"""What one build-cache entry holds on disk: the committed result of a recipe's stage that a build made below its target.

	A build-cache entry is not an image; a later build whose stage has the same build key reuses it.
	"""

	id: str
	"""The entry's digest."""

	created: datetime
	"""When a build made the entry."""

	last_used: datetime
	"""When a build last made or found the entry."""

	size_bytes: int
	"""The size on disk of the entry's own disk file."""

	in_use: bool
	"""Whether an image, machine or other entry is built on the entry, or a running build has made or found it."""

	reclaimable: bool
	"""Whether ``builder prune`` would delete the entry."""

	parent_id: str | None = None
	"""The digest of the image or build-cache entry the entry is built on."""


@dataclass(frozen=True, kw_only=True, slots=True)
class ImageFolderSpace:
	"""The free space of the volume that holds the image folder."""

	path: str
	free_bytes: int
	capacity_bytes: int
	reserve_bytes: int


@dataclass(frozen=True, kw_only=True, slots=True)
class StorageRootSpace:
	"""What the service's storage root holds, and the free space of its volume."""

	path: str
	size_bytes: int
	free_bytes: int


@dataclass(frozen=True, kw_only=True, slots=True)
class DiskUsage:
	"""What the images, machines, named volumes and build cache hold on disk: ``endia system df``."""

	images: list[ImageUsage]
	machines: list[MachineUsage]
	volumes: list[VolumeUsage]
	image_folder: ImageFolderSpace
	storage_root: StorageRootSpace
	build_cache: list[BuildCacheUsage] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True, slots=True)
class PruneResult:
	"""What a prune deleted."""

	deleted_machines: list[str]
	deleted_images: list[str]
	deleted_volumes: list[str]
	deleted_operations: int
	deleted_orphans: list[str]
	reclaimed_bytes: int
	deleted_build_cache: list[str] = field(default_factory=list)
	"""The build-cache entries deleted, by digest: the results of recipe stages below a build's target that nothing used."""


class SystemClient:
	"""The disk space routes: ``system df``, which answers at once, and ``system prune``, which starts an operation."""

	def __init__(self, connection: Connection, operations: OperationsClient) -> None:
		self._connection = connection
		self._operations = operations

	def disk_usage(self) -> DiskUsage:
		"""Reads what the images, machines, named volumes and build cache hold on disk."""
		return _wire.read(self._connection.read("/v1/system/df"), DiskUsage)

	def prune(self, *, all: bool = False, volumes: bool = False) -> OperationHandle[PruneResult]:
		"""Starts deleting what is not in use: exited machines, images without tags, children or machines, unused build cache and old operations.

		This deletes what every caller of the service owns, not only the caller's own.

		Args:
			all: Also delete tagged images that no machine or image uses.
			volumes: Also delete named volumes that no machine uses.
		"""
		query = Query().flag("all", all).flag("volumes", volumes)
		return handle_of(self._operations, self._connection.change("POST", "/v1/system/prune" + str(query)), PruneResult)
