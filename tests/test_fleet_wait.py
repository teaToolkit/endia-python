"""``FleetsClient.wait``, through the fake service: what ``endia compose up --wait`` waits for once ``up`` has succeeded."""

import re
from collections.abc import Iterator
from typing import Any

import pytest
from aiohttp import web
from fake_service import FakeService

from endia import EastIndiaClient, MachineFailedError
from endia import fleets as fleets_module

FLEET = "sdk-python-lab"
MODEL = {
	"name": FLEET,
	"services": {
		"server": {"image": "eastindia/development:latest"},
		"client": {"image": "eastindia/development:latest", "depends_on": {"server": {"condition": "service_healthy"}}},
		"spare": {"image": "eastindia/development:latest"},
	},
}


def machine(
	service: str, *, state: str = "running", ready: bool = True, health: str = "none", exit_code: int | None = None
) -> dict[str, Any]:
	return {
		"id": "0" * 32,
		"name": f"{FLEET}-{service}-1",
		"fleet": FLEET,
		"service": service,
		"index": 1,
		"image": "eastindia/development:latest",
		"imageDigest": "sha256:" + "c" * 64,
		"command": "",
		"created": "2026-11-04T09:00:00.0000000Z",
		"state": state,
		"status": "Up 1 minute",
		"ready": ready,
		"health": health,
		"ports": [],
		"exitCode": exit_code,
	}


def fleet(*machines: dict[str, Any]) -> dict[str, Any]:
	return {
		"name": FLEET,
		"configFiles": [],
		"projectDirectory": "/lab",
		"model": MODEL,
		"networks": [],
		"volumes": [],
		"machines": list(machines),
		"created": "2026-11-04T09:00:00.0000000Z",
		"updated": "2026-11-04T09:00:00.0000000Z",
	}


def readings(*answers: dict[str, Any]) -> Any:
	"""Answers each read of the fleet with the next answer, and keeps giving the last one."""
	remaining: Iterator[dict[str, Any]] = iter(answers)
	last = answers[-1]

	async def handler(_: web.Request) -> web.Response:
		nonlocal last
		last = next(remaining, last)
		return web.json_response(last)

	return handler


@pytest.fixture(autouse=True)
def no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr(fleets_module, "_WAIT_POLL_SECONDS", 0.0)


def test_wait_returns_once_the_selected_machines_and_their_dependencies_are_ready_and_healthy(
	service: FakeService, client: EastIndiaClient
) -> None:
	service.route(
		"GET",
		f"/v1/fleets/{FLEET}",
		readings(
			fleet(machine("server", health="starting"), machine("client", ready=False), machine("spare", ready=False)),
			fleet(machine("server", health="healthy"), machine("client", ready=False), machine("spare", ready=False)),
			fleet(machine("server", health="healthy"), machine("client"), machine("spare", ready=False)),
		),
	)

	machines = client.fleets.wait(FLEET, services=["client"], timeout=30)

	assert sorted(machine.service for machine in machines) == ["client", "server"]
	assert len(service.requests_to("GET", f"/v1/fleets/{FLEET}")) == 3


@pytest.mark.parametrize(
	("failing", "message"),
	[
		(machine("server", state="exited", ready=False, exit_code=3), "machine sdk-python-lab-server-1 is exited (3)"),
		(machine("server", health="unhealthy"), "machine sdk-python-lab-server-1 is unhealthy"),
	],
)
def test_wait_raises_when_a_machine_exits_or_becomes_unhealthy(
	service: FakeService, client: EastIndiaClient, failing: dict[str, Any], message: str
) -> None:
	service.route("GET", f"/v1/fleets/{FLEET}", readings(fleet(failing, machine("client"), machine("spare"))))

	with pytest.raises(MachineFailedError, match=re.escape(message)) as raised:
		client.fleets.wait(FLEET)

	assert raised.value.machine.service == "server"


def test_wait_that_runs_out_of_time_raises_timeout_error(service: FakeService, client: EastIndiaClient) -> None:
	service.route("GET", f"/v1/fleets/{FLEET}", readings(fleet(machine("server", ready=False), machine("client"), machine("spare"))))

	with pytest.raises(TimeoutError):
		client.fleets.wait(FLEET, timeout=0)


def test_wait_for_a_service_not_in_the_model_is_refused(service: FakeService, client: EastIndiaClient) -> None:
	service.route("GET", f"/v1/fleets/{FLEET}", readings(fleet(machine("server"), machine("client"), machine("spare"))))

	with pytest.raises(ValueError, match="no such service: nowhere"):
		client.fleets.wait(FLEET, services=["nowhere"])
