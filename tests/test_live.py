"""Live tests against the real service, which run only when EASTINDIA_LIVE=1 and connect where the ``endia`` CLI would.

The service is shared, so these tests touch only what they create, and name it with the prefix ``sdk-python-``: one small image,
built over ``eastindia/development:latest`` and removed again, and one fleet of one machine with 4 GiB of memory. The build runs
before the fleet comes up, so that at most one machine runs at a time. The fleet is removed when the module ends, whether its
tests passed or failed, and the last check is that nothing with the prefix is left.
"""

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fake_service import Authority, ExtendedKeyUsageOID

from endia import (
	ConnectionFailure,
	EastIndiaApiError,
	EastIndiaClient,
	EastIndiaConnectionError,
	ErrorCode,
	ExecTimeoutError,
)
from endia._environment import choose
from endia.machines import MachineState

pytestmark = pytest.mark.live

PREFIX = "sdk-python-"
IMAGE = "sdk-python-live/marker:1"
FLEET = "sdk-python-live"
MACHINE = "sdk-python-live-lab-1"
BASE = "eastindia/development:latest"

# The longest wait for a fleet to come up and for its machine's desktop user to be signed in. Booting Windows and signing the user
# in takes a few minutes on the owner's PC.
READY_SECONDS = 900
POLL_SECONDS = 5


@pytest.fixture(scope="module")
def client() -> Iterator[EastIndiaClient]:
	with EastIndiaClient.from_env() as connected:
		assert not leftovers(connected), "something with the prefix sdk-python- is left from an earlier run"
		yield connected
		assert not leftovers(connected)


@pytest.fixture(scope="module")
def machine(client: EastIndiaClient) -> Iterator[str]:
	model = {
		"name": FLEET,
		"services": {"lab": {"image": BASE, "mem_limit": "4g", "networks": ["lab"]}},
		"networks": {"lab": {"internal": True}},
	}
	try:
		client.fleets.up(model, build="never").result(timeout=READY_SECONDS)
		wait_until_desktop_ready(client, MACHINE)
		yield MACHINE
	finally:
		down = client.fleets.down(FLEET, volumes=True, timeout=30).result(timeout=READY_SECONDS)
		assert MACHINE in down.machines


def test_version_names_the_service_and_api_version_1(client: EastIndiaClient) -> None:
	version = client.version()

	assert version.api_version == "1"
	assert version.version


def test_info_describes_the_host_and_the_configuration_in_effect(client: EastIndiaClient) -> None:
	info = client.info()

	assert info.server_version == client.version().version
	assert info.config.listen_port > 0


def test_image_list_and_inspect_agree_on_the_base(client: EastIndiaClient) -> None:
	listed = {tag: image.id for image in client.images.list() for tag in image.repo_tags}

	assert client.images.inspect(BASE).id == listed[BASE]


def test_build_from_a_folder_makes_an_image_that_is_then_removed(client: EastIndiaClient, tmp_path: Path) -> None:
	(tmp_path / "Dockerfile").write_text(f"FROM {BASE}\nCOPY marker.txt C:/sdk-python/marker.txt\n", encoding="utf-8")
	(tmp_path / "marker.txt").write_text("made by the Python SDK's live tests\n", encoding="utf-8")
	(tmp_path / ".dockerignore").write_text("scratch\n", encoding="utf-8")
	(tmp_path / "scratch").write_text("left out\n", encoding="utf-8")
	try:
		built = client.images.build(tmp_path, tags=[IMAGE], labels={"sdk": "python"}).result(timeout=1800)
		assert client.images.inspect(IMAGE).id == built.digest
		assert client.images.inspect(IMAGE).labels["sdk"] == "python"
	finally:
		removed = client.images.remove(IMAGE)
	assert any(entry.untagged == IMAGE for entry in removed)


def test_attached_exec_gives_both_streams_and_an_exit_code_above_255(client: EastIndiaClient, machine: str) -> None:
	result = client.execs.run(machine, ["cmd", "/c", "echo out& echo err 1>&2& exit 3010"])

	assert result.exit_code == 3010
	assert result.stdout.strip() == b"out"
	assert result.stderr.strip() == b"err"


def test_attached_exec_reads_its_standard_input(client: EastIndiaClient, machine: str) -> None:
	result = client.execs.run(machine, ["findstr", "line"], stdin=b"first line\r\nsecond\r\n")

	assert result.stdout.strip() == b"first line"
	assert result.exit_code == 0


def test_attached_exec_past_its_timeout_is_ended_with_its_children(client: EastIndiaClient, machine: str) -> None:
	# cmd starts ping, which would run for about five minutes; the time limit ends both.
	with pytest.raises(ExecTimeoutError) as raised:
		client.execs.run(machine, ["cmd", "/c", "echo started& ping -n 300 127.0.0.1 >nul"], timeout=10)

	assert raised.value.stdout.strip() == b"started"
	ended = client.execs.inspect(raised.value.exec_id)
	deadline = time.monotonic() + 60
	while ended.running and time.monotonic() < deadline:
		time.sleep(2)
		ended = client.execs.inspect(raised.value.exec_id)
	assert not ended.running
	still = client.execs.run(machine, ["cmd", "/c", 'tasklist /fi "imagename eq ping.exe" /nh'])
	assert b"ping.exe" not in still.stdout


def test_detached_exec_is_waited_for_by_its_process(client: EastIndiaClient, machine: str) -> None:
	started = client.execs.run_detached(machine, ["cmd", "/c", "echo detached& exit 7"])

	exit_code = client.processes.wait(machine, started.process_id, timeout=120, started=started.started)

	assert exit_code == 7
	assert [line.line for line in client.processes.logs(machine, started.process_id)] == ["detached"]


def test_copy_in_and_out_keeps_a_folders_files(client: EastIndiaClient, machine: str, tmp_path: Path) -> None:
	source = tmp_path / "in"
	(source / "nested").mkdir(parents=True)
	(source / "a.txt").write_bytes(b"alpha\r\n")
	(source / "nested" / "b.bin").write_bytes(bytes(range(256)))
	client.archives.copy_in(machine, source, "C:/sdk-python-copy")

	stat = client.archives.stat(machine, "C:/sdk-python-copy/nested/b.bin")
	back = tmp_path / "out"
	back.mkdir()
	client.archives.copy_out(machine, "C:/sdk-python-copy/.", back)

	assert stat is not None
	assert stat.size == 256
	assert (back / "a.txt").read_bytes() == b"alpha\r\n"
	assert (back / "nested" / "b.bin").read_bytes() == bytes(range(256))
	client.archives.delete(machine, "C:/sdk-python-copy")
	assert client.archives.stat(machine, "C:/sdk-python-copy") is None


def test_desktop_launch_then_wait_finds_the_programs_window(client: EastIndiaClient, machine: str) -> None:
	launched = client.desktop.launch(machine, "notepad.exe")
	try:
		found = client.desktop.wait(machine, launched.process_id, "Window", timeout=120, started=launched.started)
		assert found.element is not None
		assert found.element.control_type == "Window"
	finally:
		client.processes.stop(machine, launched.process_id, timeout=0, started=launched.started)


def test_refusal_carries_its_code(client: EastIndiaClient, machine: str) -> None:
	with pytest.raises(EastIndiaApiError) as refused:
		client.desktop.invoke(machine, 4, "Window[sdk-python-no-such-window]/Button[OK]")

	assert refused.value.code in {ErrorCode.NOT_FOUND, ErrorCode.ELEMENT_NOT_FOUND}


def test_unknown_machine_is_refused_with_not_found(client: EastIndiaClient) -> None:
	with pytest.raises(EastIndiaApiError) as refused:
		client.machines.inspect("sdk-python-no-such-machine")

	assert refused.value.status_code == 404
	assert refused.value.code == ErrorCode.NOT_FOUND


def test_client_certificate_the_service_did_not_issue_is_refused(tmp_path: Path) -> None:
	stranger = Authority.create(tmp_path / "stranger", "Another authority")
	cert_file, key_file = stranger.issue("client", ExtendedKeyUsageOID.CLIENT_AUTH)
	service = choose(os.environ)

	with (
		EastIndiaClient(f"tcp://{service.host}:{service.port}", ca_file=service.ca_file, cert_file=cert_file, key_file=key_file) as refused,
		pytest.raises(EastIndiaConnectionError) as failed,
	):
		refused.version()

	assert failed.value.failure is ConnectionFailure.CERTIFICATE_REFUSED


def wait_until_desktop_ready(client: EastIndiaClient, machine: str) -> None:
	deadline = time.monotonic() + READY_SECONDS
	while time.monotonic() < deadline:
		detail = client.machines.inspect(machine)
		if detail.state == MachineState.RUNNING and detail.desktop_ready:
			return
		if detail.state == MachineState.EXITED:
			raise AssertionError(f"{machine} exited with {detail.exit_code}: {detail.last_error}")
		time.sleep(POLL_SECONDS)
	raise AssertionError(f"{machine} was not ready within {READY_SECONDS} seconds")


def leftovers(client: EastIndiaClient) -> list[str]:
	machines = [machine.name for machine in client.machines.list(all=True) if machine.name.startswith(PREFIX)]
	fleets = [fleet.name for fleet in client.fleets.list(all=True) if fleet.name.startswith(PREFIX)]
	images = [tag for image in client.images.list() for tag in image.repo_tags if tag.startswith(PREFIX)]
	volumes = [volume.name for volume in client.volumes.list() if volume.name.startswith(PREFIX)]
	return machines + fleets + images + volumes
