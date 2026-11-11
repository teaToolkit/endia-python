"""Fixtures shared by the unit tests: test authorities, the fake service, and a client connected to it."""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fake_service import Authority, ExtendedKeyUsageOID, FakeService, FakeServiceThread, server_tls

from endia import EastIndiaClient

_PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


@dataclass(frozen=True)
class Certificates:
	"""EastIndia's test authority with a client certificate it signed, and a second authority that EastIndia does not trust."""

	eastindia: Authority
	client: tuple[Path, Path]
	stranger: Authority
	stranger_client: tuple[Path, Path]


@pytest.fixture(scope="session")
def certificates(tmp_path_factory: pytest.TempPathFactory) -> Certificates:
	folder = tmp_path_factory.mktemp("certificates")
	eastindia = Authority.create(folder / "eastindia", "EastIndia test authority")
	stranger = Authority.create(folder / "stranger", "Another authority")
	return Certificates(
		eastindia=eastindia,
		client=eastindia.issue("client", ExtendedKeyUsageOID.CLIENT_AUTH),
		stranger=stranger,
		stranger_client=stranger.issue("stranger-client", ExtendedKeyUsageOID.CLIENT_AUTH),
	)


@pytest.fixture(scope="session")
def running_service(certificates: Certificates) -> Iterator[FakeService]:
	server_certificate, server_key = certificates.eastindia.issue("server", ExtendedKeyUsageOID.SERVER_AUTH, address="127.0.0.1")
	thread = FakeServiceThread(server_tls(certificates.eastindia, server_certificate, server_key))
	service = thread.start()
	yield service
	thread.stop()


@pytest.fixture
def service(running_service: FakeService) -> FakeService:
	"""The fake service with no routes and no recorded requests, so that each test supplies its own."""
	running_service.routes.clear()
	running_service.requests.clear()
	return running_service


@pytest.fixture
def no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
	"""The fake service listens on this computer, which no proxy reaches."""
	for variable in _PROXY_VARIABLES:
		monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def client(service: FakeService, certificates: Certificates, no_proxy: None) -> Iterator[EastIndiaClient]:
	cert_file, key_file = certificates.client
	with EastIndiaClient(
		f"tcp://127.0.0.1:{service.port}",
		ca_file=certificates.eastindia.ca_file,
		cert_file=cert_file,
		key_file=key_file,
		retry_delay=0.01,
	) as connected:
		yield connected


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
	"""Skips the live tests unless EASTINDIA_LIVE=1, because they drive the real service on the owner's PC."""
	if os.environ.get("EASTINDIA_LIVE") == "1":
		return
	skip = pytest.mark.skip(reason="a live test; set EASTINDIA_LIVE=1 to run it against the service")
	for item in items:
		if "live" in item.keywords:
			item.add_marker(skip)
