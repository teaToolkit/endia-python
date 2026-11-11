"""The connection's rules, through the fake service: refusals and failed connections become the SDK's two exceptions, the server
is trusted only through EastIndia's authority, and a changing request is sent again with the same Idempotency-Key.
"""

import base64
import json
import ssl

import pytest
from aiohttp import web
from conftest import Certificates
from fake_service import ExtendedKeyUsageOID, FakeService, FakeServiceThread, server_tls

from endia import ConnectionFailure, EastIndiaApiError, EastIndiaClient, EastIndiaConnectionError, ErrorCode

NOT_FOUND = {"code": "NOT_FOUND", "message": "The image lab/missing:1 does not exist.", "fieldPath": "reference", "details": None}

# Examples of use


def test_read_answer_with_unknown_members_is_read_into_the_record(service: FakeService, client: EastIndiaClient) -> None:
	async def version(_: web.Request) -> web.Response:
		return web.json_response(
			{
				"version": "0.16.9",
				"apiVersion": "1",
				"gitCommit": "35c4f8f",
				"built": "2026-10-31T21:59:35.0000000Z",
				"os": "windows",
				"arch": "amd64",
				"guestServiceVersion": "0.16.9",
				"memberFromANewerService": True,
			},
		)

	service.route("GET", "/v1/version", version)

	read = client.version()

	assert (read.version, read.git_commit, read.guest_service_version) == ("0.16.9", "35c4f8f", "0.16.9")


def test_changing_request_after_a_dropped_connection_is_sent_again_with_the_same_key(service: FakeService, client: EastIndiaClient) -> None:
	async def tag(request: web.Request) -> web.Response:
		# The first attempt loses its connection before any reply, as when the network drops.
		if len(service.requests_to("PUT", request.path)) == 1:
			assert request.transport is not None
			request.transport.abort()
		return web.json_response({"target": "lab/app:2", "digest": "sha256:" + "a" * 64, "previousDigest": None})

	service.route("PUT", "/v1/images/tags/lab/app:2", tag)

	tagged = client.images.tag("lab/app:1", "lab/app:2")

	attempts = service.requests_to("PUT", "/v1/images/tags/lab/app:2")
	keys = {attempt.headers["Idempotency-Key"] for attempt in attempts}
	assert tagged.target == "lab/app:2"
	assert len(attempts) == 2
	assert len(keys) == 1
	assert len(keys.pop()) == 32


def test_secret_value_is_sent_once_without_a_key(service: FakeService, client: EastIndiaClient) -> None:
	async def set_value(_: web.Request) -> web.Response:
		return web.json_response({"value": None})

	service.route("POST", "/v1/machines/lab-1/desktop/set-value", set_value)

	client.desktop.set_secret("lab-1", 4120, "Window/Edit[Password]", "Tr41n!ng")

	(sent,) = service.requests_to("POST", "/v1/machines/lab-1/desktop/set-value")
	assert "Idempotency-Key" not in sent.headers
	assert json.loads(sent.body) == {"processId": 4120, "element": "Window/Edit[Password]", "value": "Tr41n!ng", "secret": True}


# Examples of misuse


def test_refusal_becomes_api_error_with_code_and_field(service: FakeService, client: EastIndiaClient) -> None:
	with pytest.raises(EastIndiaApiError) as refused:
		client.images.inspect("lab/missing:1")

	assert refused.value.status_code == 404
	assert refused.value.code == ErrorCode.NOT_FOUND
	assert refused.value.error is not None
	assert refused.value.error.field_path is None
	assert "NOT_FOUND" in str(refused.value)


def test_refused_head_reads_its_error_from_the_header(service: FakeService, client: EastIndiaClient) -> None:
	async def stat(_: web.Request) -> web.Response:
		error = {"code": "MACHINE_NOT_RUNNING", "message": "The machine lab-1 is not running.", "fieldPath": None}
		return web.Response(status=409, headers={"X-EastIndia-Error": base64.b64encode(json.dumps(error).encode()).decode()})

	service.route("HEAD", "/v1/machines/lab-1/archive", stat)

	with pytest.raises(EastIndiaApiError) as refused:
		client.archives.stat("lab-1", "C:/data")

	assert refused.value.code == ErrorCode.MACHINE_NOT_RUNNING


def test_refused_request_is_not_sent_again(service: FakeService, client: EastIndiaClient) -> None:
	async def remove(_: web.Request) -> web.Response:
		return web.json_response(NOT_FOUND, status=404)

	service.route("DELETE", "/v1/images/lab/missing:1", remove)

	with pytest.raises(EastIndiaApiError):
		client.images.remove("lab/missing:1")

	assert len(service.requests_to("DELETE", "/v1/images/lab/missing:1")) == 1


def test_read_is_not_sent_again_after_a_dropped_connection(service: FakeService, client: EastIndiaClient) -> None:
	async def drop(request: web.Request) -> web.Response:
		assert request.transport is not None
		request.transport.abort()
		return web.Response()

	service.route("GET", "/v1/info", drop)

	with pytest.raises(EastIndiaConnectionError) as failed:
		client.info()

	assert failed.value.failure is ConnectionFailure.UNREACHABLE
	assert len(service.requests_to("GET", "/v1/info")) == 1


def test_changing_request_that_never_reaches_the_service_fails_unreachable_after_its_retries(
	certificates: Certificates, no_proxy: None
) -> None:
	cert_file, key_file = certificates.client
	# Port 1 on this computer has no listener, so every attempt is refused before any reply.
	with (
		EastIndiaClient(
			"tcp://127.0.0.1:1",
			ca_file=certificates.eastindia.ca_file,
			cert_file=cert_file,
			key_file=key_file,
			retry_delay=0.01,
		) as unreachable,
		pytest.raises(EastIndiaConnectionError) as failed,
	):
		unreachable.machines.start("lab-1")

	assert failed.value.failure is ConnectionFailure.UNREACHABLE
	assert str(failed.value) == "Cannot connect to the EastIndia service at 127.0.0.1:1. Is the service running?"


def test_server_certificate_from_another_authority_is_not_trusted(service: FakeService, certificates: Certificates, no_proxy: None) -> None:
	cert_file, key_file = certificates.client
	with (
		EastIndiaClient(
			f"tcp://127.0.0.1:{service.port}",
			ca_file=certificates.stranger.ca_file,
			cert_file=cert_file,
			key_file=key_file,
		) as misled,
		pytest.raises(EastIndiaConnectionError) as failed,
	):
		misled.version()

	assert failed.value.failure is ConnectionFailure.SERVER_NOT_TRUSTED
	assert service.requests == []


def test_client_certificate_from_another_authority_is_refused(certificates: Certificates, no_proxy: None) -> None:
	# Under TLS 1.3 the refusal arrives after the handshake, and a local server that closes its socket at once can reset the
	# connection before the client reads the alert. This service speaks TLS 1.2, which refuses within the handshake; the live
	# tests show the real service's refusal under TLS 1.3.
	certificate, key = certificates.eastindia.issue("tls12-server", ExtendedKeyUsageOID.SERVER_AUTH, address="127.0.0.1")
	tls = server_tls(certificates.eastindia, certificate, key)
	tls.maximum_version = ssl.TLSVersion.TLSv1_2
	thread = FakeServiceThread(tls)
	service = thread.start()
	cert_file, key_file = certificates.stranger_client
	try:
		with (
			EastIndiaClient(
				f"tcp://127.0.0.1:{service.port}",
				ca_file=certificates.eastindia.ca_file,
				cert_file=cert_file,
				key_file=key_file,
			) as stranger,
			pytest.raises(EastIndiaConnectionError) as failed,
		):
			stranger.version()
	finally:
		thread.stop()

	assert failed.value.failure is ConnectionFailure.CERTIFICATE_REFUSED
	assert service.requests == []
