"""Execs: Docker's multiplexed frames, checked against bytes written out by hand, and attached runs through the fake service's
WebSocket.
"""

import json

import pytest
from aiohttp import WSMsgType, web
from fake_service import FakeService

from endia import EastIndiaApiError, EastIndiaClient, ErrorCode, ExecTimeoutError, StreamKind
from endia.execs import decode_frame, encode_frame

EXEC_ID = "5d0c1f4ba3e94f7c8a52d1b0e6f7a9c3"

# Examples of use


def test_frame_of_standard_error_has_the_stream_padding_and_big_endian_length() -> None:
	assert encode_frame(StreamKind.STDERR, b"denied\r\n") == b"\x02\x00\x00\x00\x00\x00\x00\x08denied\r\n"


def test_frame_of_standard_output_reads_back_its_stream_and_payload() -> None:
	assert decode_frame(b"\x01\x00\x00\x00\x00\x00\x00\x05ready") == (StreamKind.STDOUT, b"ready")


def test_empty_standard_input_frame_is_a_header_alone() -> None:
	assert encode_frame(StreamKind.STDIN, b"") == bytes(8)


def test_run_collects_both_streams_and_the_full_exit_code(service: FakeService, client: EastIndiaClient) -> None:
	_serve_exec(service, frames=[(1, b"Installing\r\n"), (2, b"restart needed\r\n"), (1, b"Done\r\n")], exit_code=3010)
	seen: list[tuple[StreamKind, bytes]] = []

	result = client.execs.run(
		"lab-1", ["msiexec.exe", "/i", "C:\\setup\\app.msi", "/qn"], on_output=lambda kind, data: seen.append((kind, data))
	)

	(created,) = service.requests_to("POST", "/v1/machines/lab-1/exec")
	assert json.loads(created.body) == {
		"cmd": ["msiexec.exe", "/i", "C:\\setup\\app.msi", "/qn"],
		"env": [],
		"tty": False,
		"attachStdin": False,
	}
	assert result.exit_code == 3010
	assert result.stdout == b"Installing\r\nDone\r\n"
	assert result.stderr == b"restart needed\r\n"
	assert seen[1] == (StreamKind.STDERR, b"restart needed\r\n")


def test_run_with_input_sends_it_as_frames_then_closes_standard_input(service: FakeService, client: EastIndiaClient) -> None:
	_serve_exec(service, frames=[], exit_code=0, echo_input=True)

	result = client.execs.run("lab-1", ["findstr", "x"], stdin=b"one\r\ntwo\r\n")

	assert result.stdout == b"one\r\ntwo\r\n"
	assert result.exit_code == 0


def test_program_that_was_not_found_ends_with_127_and_its_reason(service: FakeService, client: EastIndiaClient) -> None:
	_serve_exec(service, frames=[], exit_code=127, start_error="The program nothere.exe was not found.")

	result = client.execs.run("lab-1", ["nothere.exe"])

	assert (result.exit_code, result.start_error) == (127, "The program nothere.exe was not found.")


# Examples of misuse


def test_message_that_is_not_one_whole_frame_is_not_read() -> None:
	assert decode_frame(b"\x01\x00\x00") is None
	assert decode_frame(b"\x03\x00\x00\x00\x00\x00\x00\x00") is None
	assert decode_frame(b"\x01\x00\x01\x00\x00\x00\x00\x00") is None
	assert decode_frame(b"\x01\x00\x00\x00\x00\x00\x00\x09short") is None


def test_refused_upgrade_reads_the_refusal_from_its_body(service: FakeService, client: EastIndiaClient) -> None:
	async def refuse(_: web.Request) -> web.Response:
		return web.json_response(
			{"code": "MACHINE_NOT_RUNNING", "message": "The machine lab-1 is not running.", "fieldPath": None}, status=409
		)

	service.route("GET", f"/v1/execs/{EXEC_ID}/start", refuse)

	with pytest.raises(EastIndiaApiError) as refused:
		client.execs.attach(EXEC_ID)

	assert refused.value.status_code == 409
	assert refused.value.code == ErrorCode.MACHINE_NOT_RUNNING


def _serve_exec(
	service: FakeService, *, frames: list[tuple[int, bytes]], exit_code: int, start_error: str | None = None, echo_input: bool = False
) -> None:
	"""Answers one exec in machine lab-1: its creation, its attached start, which sends the frames and closes, and its state."""

	async def create(_: web.Request) -> web.Response:
		return web.json_response({"id": EXEC_ID})

	async def start(request: web.Request) -> web.WebSocketResponse:
		socket = web.WebSocketResponse()
		await socket.prepare(request)
		for stream, payload in frames:
			await socket.send_bytes(encode_frame(StreamKind(stream), payload))
		if echo_input:
			async for message in socket:
				if message.type is not WSMsgType.BINARY:
					break
				frame = decode_frame(message.data)
				assert frame is not None
				assert frame[0] is StreamKind.STDIN
				if not frame[1]:
					break
				await socket.send_bytes(encode_frame(StreamKind.STDOUT, frame[1]))
		await socket.close(code=1000)
		return socket

	async def inspect(_: web.Request) -> web.Response:
		return web.json_response(
			{
				"id": EXEC_ID,
				"machine": "lab-1",
				"cmd": ["program"],
				"tty": False,
				"detached": False,
				"running": False,
				"exitCode": exit_code,
				"startError": start_error,
			},
		)

	service.route("POST", "/v1/machines/lab-1/exec", create)
	service.route("GET", f"/v1/execs/{EXEC_ID}/start", start)
	service.route("GET", f"/v1/execs/{EXEC_ID}", inspect)


def test_run_past_its_timeout_stops_the_program_tree_and_raises_with_the_output_so_far(
	service: FakeService, client: EastIndiaClient
) -> None:
	async def create(_: web.Request) -> web.Response:
		return web.json_response({"id": EXEC_ID})

	async def start(request: web.Request) -> web.WebSocketResponse:
		socket = web.WebSocketResponse()
		await socket.prepare(request)
		await socket.send_bytes(encode_frame(StreamKind.STDOUT, b"waiting for a dialog\r\n"))
		async for _ in socket:  # the program hangs until the client closes the socket
			pass
		return socket

	async def inspect(_: web.Request) -> web.Response:
		return web.json_response(
			{
				"id": EXEC_ID,
				"machine": "lab-1",
				"cmd": ["setup.exe"],
				"tty": False,
				"detached": False,
				"running": True,
				"processId": 4242,
				"started": "2026-11-08T23:00:00Z",
			}
		)

	async def stop(_: web.Request) -> web.Response:
		return web.json_response({"exitCode": 1})

	service.route("POST", "/v1/machines/lab-1/exec", create)
	service.route("GET", f"/v1/execs/{EXEC_ID}/start", start)
	service.route("GET", f"/v1/execs/{EXEC_ID}", inspect)
	service.route("POST", "/v1/machines/lab-1/processes/4242/stop", stop)

	with pytest.raises(ExecTimeoutError) as raised:
		client.execs.run("lab-1", ["setup.exe"], timeout=0.5)

	assert raised.value.stdout == b"waiting for a dialog\r\n"
	assert raised.value.exec_id == EXEC_ID
	(stopped,) = service.requests_to("POST", "/v1/machines/lab-1/processes/4242/stop")
	assert (stopped.query["t"], stopped.query["tree"]) == (["0"], ["true"])
	assert "started" in stopped.query


def test_run_that_ends_within_its_timeout_returns_as_without_one(service: FakeService, client: EastIndiaClient) -> None:
	_serve_exec(service, frames=[(1, b"ok\r\n")], exit_code=0)

	result = client.execs.run("lab-1", ["cmd", "/c", "echo ok"], timeout=30)

	assert (result.exit_code, result.stdout) == (0, b"ok\r\n")
	assert service.requests_to("POST", "/v1/machines/lab-1/processes/4242/stop") == []
