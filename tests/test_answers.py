"""Answers recorded from service 0.16.88, read as the SDK's records. Each record declares exactly the members the service sends, so
an answer is read in full and a member the service has retired is not demanded.

The files in ``answers`` are the service's answers as it wrote them, with only the host's name replaced.
"""

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from endia._wire import read, wire_name
from endia.stats import HostStats
from endia.system import HostConfigurationView, HyperVStatus, SystemInfo, VersionInfo

ANSWERS = Path(__file__).parent / "answers"


def answer(name: str) -> dict[str, Any]:
	value: dict[str, Any] = json.loads((ANSWERS / name).read_text(encoding="utf-8"))
	return value


def members(record: type[Any]) -> set[str]:
	return {wire_name(field) for field in dataclasses.fields(record)}


# Examples of use


def test_info_answer_is_read_without_the_retired_hyper_v_access_members() -> None:
	info = read(answer("info-0.16.88.json"), SystemInfo)

	assert info.server_version == "0.16.88"
	assert info.config.build_cache_keep_gib == 20
	assert info.config.purpose_history_max_entries == 500
	assert info.config.default_address_pools is None


@pytest.mark.parametrize(
	("record", "members_of_answer"),
	[
		(SystemInfo, lambda: answer("info-0.16.88.json")),
		(HyperVStatus, lambda: answer("info-0.16.88.json")["hyperV"]),
		(HostConfigurationView, lambda: answer("info-0.16.88.json")["config"]),
		(VersionInfo, lambda: answer("version-0.16.88.json")),
		(HostStats, lambda: answer("host-stats-0.16.88.json")),
	],
	ids=lambda value: getattr(value, "__name__", ""),
)
def test_record_declares_exactly_the_members_the_service_sends(record: type[Any], members_of_answer: Any) -> None:
	assert members(record) == set(members_of_answer())


def test_host_stats_answer_carries_the_disk_and_network_rates() -> None:
	stats = read(answer("host-stats-0.16.88.json"), HostStats)

	assert stats.disk_bytes_per_second == pytest.approx(91526818.69639885)
	assert stats.network_bytes_per_second == pytest.approx(56420.059582919566)
