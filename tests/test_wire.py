"""The API's times and member names: a time the service wrote goes back exactly as written, and records skip unknown members."""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from endia import Timestamp
from endia._wire import camel, format_time, read
from endia.system import HostConfigurationView

# Examples of use


def test_time_with_seven_fractional_digits_is_sent_back_exactly_as_written() -> None:
	# Windows keeps a process's start to 100 nanoseconds; a datetime holds microseconds, so the seventh digit would be lost.
	started = Timestamp.parse("2026-10-05T14:03:22.1234567Z")

	assert started == datetime(2026, 10, 5, 14, 3, 22, 123456, tzinfo=timezone.utc)
	assert format_time(started) == "2026-10-05T14:03:22.1234567Z"


def test_time_with_an_offset_is_read_as_the_same_instant_in_utc() -> None:
	assert Timestamp.parse("2026-10-05T15:03:22+01:00") == datetime(2026, 10, 5, 14, 3, 22, tzinfo=timezone.utc)


def test_aware_datetime_is_written_in_utc_with_seven_digits() -> None:
	local = datetime(2026, 10, 5, 15, 3, 22, 500000, tzinfo=timezone(timedelta(hours=1)))

	assert format_time(local) == "2026-10-05T14:03:22.5000000Z"


def test_field_names_become_camel_case_members() -> None:
	assert camel("field_path") == "fieldPath"
	assert camel("ipv4_address") == "ipv4Address"
	assert camel("id") == "id"


def test_member_whose_case_differs_from_the_rule_is_read_by_its_own_name() -> None:
	members = {name: 0 for name in HostConfigurationView.__dataclass_fields__}
	wire: dict[str, Any] = {camel(name): value for name, value in members.items()}
	for field, member in [
		("host_reserve_mib", "hostReserveMiB"),
		("min_free_disk_gib", "minFreeDiskGiB"),
		("builder_memory_mib", "builderMemoryMiB"),
		("base_prepare_disk_gib", "basePrepareDiskGiB"),
		("machine_memory_mib", "machineMemoryMiB"),
		("machine_memory_floor_mib", "machineMemoryFloorMiB"),
		("machine_memory_ceiling_mib", "machineMemoryCeilingMiB"),
		("build_cache_keep_gib", "buildCacheKeepGiB"),
		("volume_size_gib", "volumeSizeGiB"),
	]:
		del wire[camel(field)]
		wire[member] = 7
	for nullable in ("image_folder", "guest_programs_folder", "default_address_pools"):
		wire[camel(nullable)] = None
	wire["listenAddress"] = wire["resourcePrefix"] = wire["internetSwitch"] = "x"

	configuration = read(wire, HostConfigurationView)

	assert configuration.host_reserve_mib == 7
	assert configuration.volume_size_gib == 7


# Examples of misuse


def test_naive_datetime_is_refused_because_its_instant_is_unknown() -> None:
	with pytest.raises(ValueError, match="no zone"):
		format_time(datetime(2026, 10, 5, 14, 3, 22))


def test_text_that_is_not_an_rfc_3339_time_is_refused() -> None:
	with pytest.raises(ValueError, match="RFC 3339"):
		Timestamp.parse("5 October 2026")
