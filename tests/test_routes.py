"""Routes whose requests the SDK builds itself, through the fake service: operations followed to their end, uploads, copies,
streamed logs and screenshots.
"""

import base64
import io
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from fake_service import FakeService

from endia import EastIndiaClient, ErrorCode, OperationFailedError
from endia.images import BuilderPruneResult, BuildResult, BuildStageResult

OPERATION_ID = "0f3c9a1d2b4e4f6a8c7d5e3b1a9f2c4d"
DIGEST = "sha256:" + "c" * 64


def operation(state: str, *, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
	return {
		"id": OPERATION_ID,
		"kind": "build",
		"target": "lab/app:1",
		"state": state,
		"step": None,
		"created": "2026-11-02T09:00:00.0000000Z",
		"caller": "sdk-python",
		"result": result,
		"error": error,
	}


def answer(body: Any, status: int = 200) -> Any:
	async def handler(_: web.Request) -> web.Response:
		return web.json_response(body, status=status)

	return handler


# Examples of use


def test_build_sends_the_packed_context_and_its_options_and_gives_the_result(
	service: FakeService, client: EastIndiaClient, tmp_path: Path
) -> None:
	(tmp_path / "Dockerfile").write_text("FROM eastindia/development:latest\n")
	(tmp_path / "app.txt").write_text("app")
	result = {"digest": DIGEST, "tags": ["lab/app:1"], "buildKey": "k", "cacheHit": False, "seconds": 41.5}
	service.route("POST", "/v1/images/build", answer(operation("queued"), 202))
	service.route("GET", f"/v1/operations/{OPERATION_ID}", answer(operation("succeeded", result=result)))

	built = client.images.build(tmp_path, tags=["lab/app:1"], build_args={"VERSION": "10"}, memory=4 * 1024**3).result()

	(upload,) = service.requests_to("POST", "/v1/images/build")
	assert built == BuildResult(digest=DIGEST, tags=["lab/app:1"], build_key="k", cache_hit=False, seconds=41.5)
	# The recipe is always named, as the CLI names it, so that the service cannot choose another.
	assert upload.query == {"t": ["lab/app:1"], "dockerfile": ["Dockerfile"], "buildargs": ['{"VERSION":"10"}'], "memory": ["4294967296"]}
	assert upload.headers["Content-Type"] == "application/x-tar"
	with tarfile.open(fileobj=io.BytesIO(upload.body)) as tar:
		assert tar.getnames() == ["Dockerfile", "app.txt"]


def test_build_with_a_target_names_the_stage_and_reads_what_each_stage_gave(
	service: FakeService, client: EastIndiaClient, tmp_path: Path
) -> None:
	(tmp_path / "Endiafile").write_text("FROM eastindia/development:latest AS tools\nRUN setup.cmd\n\nFROM tools AS app\nRUN app.cmd\n")
	stage = {"name": "tools", "digest": DIGEST, "buildKey": "k1", "cacheHit": True}
	result = {"digest": DIGEST, "tags": ["lab/tools:1"], "buildKey": "k1", "cacheHit": True, "seconds": 0.4, "stages": [stage]}
	service.route("POST", "/v1/images/build", answer(operation("queued"), 202))
	service.route("GET", f"/v1/operations/{OPERATION_ID}", answer(operation("succeeded", result=result)))

	built = client.images.build(tmp_path, target="tools", tags=["lab/tools:1"]).result()

	(upload,) = service.requests_to("POST", "/v1/images/build")
	assert upload.query["target"] == ["tools"]
	assert built.stages == [BuildStageResult(name="tools", digest=DIGEST, build_key="k1", cache_hit=True)]


def test_build_without_a_target_sends_none_and_reads_a_result_without_stages(service: FakeService, client: EastIndiaClient) -> None:
	# An answer without stages comes from a service older than multi-stage builds.
	result = {"digest": DIGEST, "tags": [], "buildKey": "k", "cacheHit": False, "seconds": 1.0}
	service.route("POST", "/v1/images/build", answer(operation("queued"), 202))
	service.route("GET", f"/v1/operations/{OPERATION_ID}", answer(operation("succeeded", result=result)))

	built = client.images.build_archive(io.BytesIO(b"\0" * 1024), dockerfile="Endiafile").result()

	(upload,) = service.requests_to("POST", "/v1/images/build")
	assert "target" not in upload.query
	assert built.stages == []


def test_builder_prune_starts_the_operation_and_gives_the_entries_it_deleted(service: FakeService, client: EastIndiaClient) -> None:
	result = {"deletedBuildCache": [DIGEST], "reclaimedBytes": 7340032}
	service.route("POST", "/v1/builder/prune", answer(operation("queued"), 202))
	service.route("GET", f"/v1/operations/{OPERATION_ID}", answer(operation("succeeded", result=result)))

	pruned = client.images.prune_build_cache(all=True).result()

	(prune,) = service.requests_to("POST", "/v1/builder/prune")
	assert prune.query == {"all": ["true"]}
	assert "Idempotency-Key" in prune.headers
	assert pruned == BuilderPruneResult(deleted_build_cache=[DIGEST], reclaimed_bytes=7340032)


def test_system_prune_gives_the_build_cache_entries_it_deleted(service: FakeService, client: EastIndiaClient) -> None:
	result = {
		"deletedMachines": [],
		"deletedImages": [],
		"deletedBuildCache": [DIGEST],
		"deletedVolumes": [],
		"deletedOperations": 0,
		"deletedOrphans": [],
		"reclaimedBytes": 7340032,
	}
	service.route("POST", "/v1/system/prune", answer(operation("queued"), 202))
	service.route("GET", f"/v1/operations/{OPERATION_ID}", answer(operation("succeeded", result=result)))

	pruned = client.system.prune().result()

	assert pruned.deleted_build_cache == [DIGEST]


def test_disk_usage_lists_each_build_cache_entry(service: FakeService, client: EastIndiaClient) -> None:
	entry = {
		"id": DIGEST,
		"parentId": "sha256:" + "a" * 64,
		"created": "2026-11-10T09:00:00.0000000Z",
		"lastUsed": "2026-11-10T10:00:00.0000000Z",
		"sizeBytes": 7340032,
		"inUse": False,
		"reclaimable": True,
	}
	usage = {
		"images": [],
		"machines": [],
		"volumes": [],
		"buildCache": [entry],
		"imageFolder": {"path": "M:\\EastIndia", "freeBytes": 1, "capacityBytes": 2, "reserveBytes": 0},
		"storageRoot": {"path": "C:\\ProgramData\\EastIndia", "sizeBytes": 1, "freeBytes": 1},
	}
	service.route("GET", "/v1/system/df", answer(usage))

	(cached,) = client.system.disk_usage().build_cache

	assert (cached.id, cached.parent_id, cached.size_bytes, cached.in_use, cached.reclaimable) == (
		DIGEST,
		"sha256:" + "a" * 64,
		7340032,
		False,
		True,
	)
	assert cached.last_used == datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)


def test_fleet_up_sends_the_archive_to_the_fleet_named_by_the_model(service: FakeService, client: EastIndiaClient) -> None:
	service.route("PUT", "/v1/fleets/sdk-python-lab", answer(operation("queued"), 202))
	model = {"name": "sdk-python-lab", "services": {"lab": {"image": "eastindia/development:latest", "mem_limit": "4294967296"}}}

	handle = client.fleets.up(model, scale={"lab": 2}, recreate="force")

	(up,) = service.requests_to("PUT", "/v1/fleets/sdk-python-lab")
	assert handle.id == OPERATION_ID
	assert up.query == {"scale": ["lab=2"], "recreate": ["force"]}
	with tarfile.open(fileobj=io.BytesIO(up.body)) as tar:
		assert tar.getnames() == ["compose.json"]


def test_copy_in_of_a_file_to_a_new_name_writes_it_into_the_parent_folder(
	service: FakeService, client: EastIndiaClient, tmp_path: Path
) -> None:
	package = tmp_path / "Server.msi"
	package.write_bytes(b"MSI")

	async def stat(request: web.Request) -> web.Response:
		missing = {"code": "NOT_FOUND", "message": "Nothing is at the path.", "fieldPath": "path"}
		return web.Response(status=404, headers={"X-EastIndia-Error": base64.b64encode(json.dumps(missing).encode()).decode()})

	service.route("HEAD", "/v1/machines/lab-1/archive", stat)
	service.route("PUT", "/v1/machines/lab-1/archive", answer({}))

	client.archives.copy_in("lab-1", package, "C:/Tests/assets/Server/package.msi")

	(written,) = service.requests_to("PUT", "/v1/machines/lab-1/archive")
	assert written.query == {"path": ["C:\\Tests\\assets\\Server"]}
	with tarfile.open(fileobj=io.BytesIO(written.body)) as tar:
		assert tar.getnames() == ["package.msi"]


def test_copy_out_of_a_folders_contents_extracts_them_into_the_local_folder(
	service: FakeService, client: EastIndiaClient, tmp_path: Path
) -> None:
	stat = {"name": "results", "size": 0, "mode": "directory", "mtime": "2026-11-02T09:00:00.0000000Z", "linkTarget": None}
	header = {"X-EastIndia-Path-Stat": base64.b64encode(json.dumps(stat).encode()).decode()}
	contents = io.BytesIO()
	with tarfile.open(fileobj=contents, mode="w") as tar:
		for name, data in [("./", None), ("./run.trx", b"<TestRun/>"), ("./logs/", None), ("./logs/guest.log", b"ok")]:
			entry = tarfile.TarInfo(name)
			if data is None:
				entry.type = tarfile.DIRTYPE
				tar.addfile(entry)
			else:
				entry.size = len(data)
				tar.addfile(entry, io.BytesIO(data))

	async def head(_: web.Request) -> web.Response:
		return web.Response(headers=header)

	async def get(_: web.Request) -> web.Response:
		return web.Response(body=contents.getvalue(), headers=header, content_type="application/x-tar")

	service.route("HEAD", "/v1/machines/lab-1/archive", head)
	service.route("GET", "/v1/machines/lab-1/archive", get)

	client.archives.copy_out("lab-1", "C:/Tests/results/.", tmp_path)

	(read,) = service.requests_to("GET", "/v1/machines/lab-1/archive")
	assert read.query == {"path": ["C:\\Tests\\results\\."]}
	assert (tmp_path / "run.trx").read_bytes() == b"<TestRun/>"
	assert (tmp_path / "logs" / "guest.log").read_bytes() == b"ok"


def test_machine_log_is_read_line_by_line_as_records(service: FakeService, client: EastIndiaClient) -> None:
	lines = [
		{"time": "2026-11-02T09:00:01.0000000Z", "stream": "stdout", "line": "Listening on 8080"},
		{"time": "2026-11-02T09:00:02.0000000Z", "stream": "stderr", "line": "warning: slow disk"},
	]

	async def logs(_: web.Request) -> web.Response:
		return web.Response(text="".join(json.dumps(line) + "\n" for line in lines), content_type="application/x-ndjson")

	service.route("GET", "/v1/machines/lab-1/logs", logs)

	read = list(client.machines.logs("lab-1", tail=10))

	assert [(line.stream, line.line) for line in read] == [("stdout", "Listening on 8080"), ("stderr", "warning: slow disk")]
	assert service.requests_to("GET", "/v1/machines/lab-1/logs")[0].query == {"tail": ["10"]}


def test_screenshot_reads_the_png_and_its_headers(service: FakeService, client: EastIndiaClient) -> None:
	async def screenshot(_: web.Request) -> web.Response:
		headers = {
			"X-EastIndia-Width": "1280",
			"X-EastIndia-Height": "800",
			"X-EastIndia-Session": "1",
			"X-EastIndia-Taken": "2026-11-02T09:00:00.0000000Z",
		}
		return web.Response(body=b"\x89PNG", headers=headers, content_type="image/png")

	service.route("POST", "/v1/machines/lab-1/desktop/screenshot", screenshot)

	shot = client.desktop.screenshot("lab-1")

	assert (shot.png, shot.width, shot.height) == (b"\x89PNG", 1280, 800)
	assert "Idempotency-Key" not in service.requests_to("POST", "/v1/machines/lab-1/desktop/screenshot")[0].headers


# Examples of misuse


def test_failed_build_raises_with_the_operations_error(service: FakeService, client: EastIndiaClient, tmp_path: Path) -> None:
	(tmp_path / "Dockerfile").write_text("FROM eastindia/development:latest\n")
	error = {"code": "BUILD_FAILED", "message": "RUN at line 3 exited 1.", "fieldPath": "Dockerfile:3:1", "details": {"exitCode": 1}}
	service.route("POST", "/v1/images/build", answer(operation("queued"), 202))
	service.route("GET", f"/v1/operations/{OPERATION_ID}", answer(operation("failed", error=error)))

	with pytest.raises(OperationFailedError) as failed:
		client.images.build(tmp_path).result()

	assert failed.value.code == ErrorCode.BUILD_FAILED
	assert failed.value.error is not None
	assert failed.value.error.details == {"exitCode": 1}


def test_wait_that_outlasts_its_time_out_raises_timeout_error(service: FakeService, client: EastIndiaClient) -> None:
	service.route("GET", f"/v1/operations/{OPERATION_ID}", answer(operation("running")))

	with pytest.raises(TimeoutError, match="running"):
		client.operations.wait(OPERATION_ID, timeout=0)
