"""Purposes through the fake service: sent with ``compose up`` as the CLI sends them, set and cleared by their routes, and read in records."""

import io
import json
import tarfile
from typing import Any

import pytest
from aiohttp import web
from fake_service import FakeService

from endia import EastIndiaClient
from endia.purposes import MAX_DETAIL_BYTES, PurposeAction, PurposeSource

FLEET = "sdk-python-lab"
MODEL = {"name": FLEET, "services": {"lab": {"image": "eastindia/development:latest"}}}
STORED = {"title": "Nightly UI run", "detail": "# Notes", "setBy": "client", "set": "2026-11-04T09:00:00.1234567Z"}
OPERATION = {
	"id": "0f3c9a1d2b4e4f6a8c7d5e3b1a9f2c4d",
	"kind": "fleet.up",
	"target": FLEET,
	"state": "queued",
	"step": None,
	"created": "2026-11-04T09:00:00.0000000Z",
	"caller": "sdk-python",
	"result": None,
	"error": None,
}


def archive_of(body: bytes) -> dict[str, bytes]:
	with tarfile.open(fileobj=io.BytesIO(body)) as tar:
		files = {member.name: tar.extractfile(member) for member in tar.getmembers() if member.isfile()}
		return {name: file.read() for name, file in files.items() if file is not None}


def answering(body: Any, status: int = 200) -> Any:
	async def handler(_: web.Request) -> web.Response:
		return web.json_response(body, status=status)

	return handler


# Examples of use


def test_fleet_up_with_a_purpose_sends_its_title_and_detail_as_purpose_json(service: FakeService, client: EastIndiaClient) -> None:
	service.route("PUT", f"/v1/fleets/{FLEET}", answering(OPERATION, 202))

	client.fleets.up(MODEL, purpose="Nightly UI run", purpose_detail="# Notes\nCafé\n")

	(up,) = service.requests_to("PUT", f"/v1/fleets/{FLEET}")
	files = archive_of(up.body)
	assert sorted(files) == ["compose.json", "purpose.json"]
	assert json.loads(files["purpose.json"]) == {"title": "Nightly UI run", "detail": "# Notes\nCafé\n"}


def test_fleet_up_with_a_title_only_sends_purpose_json_without_a_detail(service: FakeService, client: EastIndiaClient) -> None:
	service.route("PUT", f"/v1/fleets/{FLEET}", answering(OPERATION, 202))

	client.fleets.up(MODEL, purpose="Nightly UI run")

	(up,) = service.requests_to("PUT", f"/v1/fleets/{FLEET}")
	assert json.loads(archive_of(up.body)["purpose.json"]) == {"title": "Nightly UI run"}


def test_fleet_up_without_a_purpose_sends_no_purpose_json(service: FakeService, client: EastIndiaClient) -> None:
	service.route("PUT", f"/v1/fleets/{FLEET}", answering(OPERATION, 202))

	client.fleets.up(MODEL)

	(up,) = service.requests_to("PUT", f"/v1/fleets/{FLEET}")
	assert list(archive_of(up.body)) == ["compose.json"]


def test_set_purpose_puts_the_title_and_detail_and_reads_the_stored_purpose(service: FakeService, client: EastIndiaClient) -> None:
	service.route(
		"PUT", f"/v1/machines/{FLEET}-lab-1/purpose", answering({"purpose": STORED, "effective": STORED, "purposeSource": "machine"})
	)

	result = client.machines.set_purpose(f"{FLEET}-lab-1", "Nightly UI run", "# Notes")

	(put,) = service.requests_to("PUT", f"/v1/machines/{FLEET}-lab-1/purpose")
	assert json.loads(put.body) == {"title": "Nightly UI run", "detail": "# Notes"}
	assert result.purpose is not None
	assert result.purpose.set_by == "client"
	assert result.purpose_source == PurposeSource.MACHINE


def test_clear_purpose_of_a_fleet_deletes_it_and_reads_no_purpose(service: FakeService, client: EastIndiaClient) -> None:
	service.route("DELETE", f"/v1/fleets/{FLEET}/purpose", answering({"purpose": None}))

	result = client.fleets.clear_purpose(FLEET)

	assert result.purpose is None
	assert len(service.requests_to("DELETE", f"/v1/fleets/{FLEET}/purpose")) == 1


def test_fleet_purpose_reads_the_purpose_and_its_history(service: FakeService, client: EastIndiaClient) -> None:
	history = [
		{"time": "2026-11-04T09:00:00.0000000Z", "action": "set", "title": "Nightly UI run", "setBy": "client"},
		{"time": "2026-11-04T10:00:00.0000000Z", "action": "cleared", "setBy": "npipe:LAB\\ann"},
	]
	service.route("GET", f"/v1/fleets/{FLEET}/purpose", answering({"purpose": None, "history": history}))

	shown = client.fleets.purpose(FLEET)

	assert shown.purpose is None
	assert [entry.action for entry in shown.history] == [PurposeAction.SET, PurposeAction.CLEARED]
	assert shown.history[1].title is None


def test_machine_purpose_names_where_the_effective_purpose_comes_from(service: FakeService, client: EastIndiaClient) -> None:
	service.route(
		"GET", "/v1/machines/lab-1/purpose", answering({"purpose": None, "history": [], "effective": STORED, "purposeSource": "fleet"})
	)

	shown = client.machines.purpose("lab-1")

	assert shown.purpose is None
	assert shown.effective is not None
	assert shown.effective.detail == "# Notes"
	assert shown.purpose_source == PurposeSource.FLEET


# Records


def test_fleet_and_machine_records_carry_the_purpose_the_service_returns(service: FakeService, client: EastIndiaClient) -> None:
	summary = {"title": "Nightly UI run", "setBy": "client", "set": "2026-11-04T09:00:00.1234567Z"}
	machine = {
		"id": "0" * 32,
		"name": f"{FLEET}-lab-1",
		"fleet": FLEET,
		"service": "lab",
		"index": 1,
		"image": "eastindia/development:latest",
		"imageDigest": "sha256:" + "c" * 64,
		"command": "",
		"created": "2026-11-04T09:00:00.0000000Z",
		"state": "running",
		"status": "Up 1 minute",
		"ready": True,
		"health": "none",
		"ports": [],
		"purpose": summary,
		"purposeSource": "fleet",
	}
	service.route("GET", "/v1/machines", answering([machine]))
	service.route("GET", "/v1/fleets", answering([{"name": FLEET, "status": "running(1)", "configFiles": [], "purpose": summary}]))

	(listed,) = client.machines.list()
	(fleet,) = client.fleets.list()

	assert listed.purpose is not None
	assert listed.purpose.title == "Nightly UI run"
	assert listed.purpose.set.microsecond == 123456
	assert listed.purpose_source == PurposeSource.FLEET
	assert fleet.purpose is not None
	assert fleet.purpose.set_by == "client"


# Refusals


def test_fleet_up_with_a_detail_and_no_title_is_refused_before_anything_is_sent(service: FakeService, client: EastIndiaClient) -> None:
	with pytest.raises(ValueError, match="purpose_detail needs purpose"):
		client.fleets.up(MODEL, purpose_detail="# Notes")

	assert service.requests == []


@pytest.mark.parametrize("title", ["two\nlines", "two\rlines", "next\u0085line", "line\u2028separator", "paragraph\u2029separator"])
def test_a_title_with_a_line_break_is_refused_before_anything_is_sent(service: FakeService, client: EastIndiaClient, title: str) -> None:
	with pytest.raises(ValueError, match="one line"):
		client.fleets.up(MODEL, purpose=title)
	with pytest.raises(ValueError, match="one line"):
		client.machines.set_purpose("lab-1", title)

	assert service.requests == []


def test_a_detail_over_64_kib_in_utf_8_is_refused_but_exactly_64_kib_is_sent(service: FakeService, client: EastIndiaClient) -> None:
	service.route("PUT", "/v1/fleets/lab/purpose", answering({"purpose": STORED}))
	two_bytes_each = "é" * (MAX_DETAIL_BYTES // 2)

	client.fleets.set_purpose("lab", "Run", two_bytes_each)
	with pytest.raises(ValueError, match="65536 bytes"):
		client.fleets.set_purpose("lab", "Run", two_bytes_each + "x")

	assert len(service.requests_to("PUT", "/v1/fleets/lab/purpose")) == 1
