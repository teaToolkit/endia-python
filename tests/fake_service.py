"""A fake EastIndia service for the unit tests: a real HTTPS server with mutual TLS, whose routes each test supplies.

The server runs aiohttp on its own thread and event loop, on a free port of 127.0.0.1. It presents a certificate for 127.0.0.1
signed by a test authority, and refuses any client certificate that the same authority did not sign. Each test registers the
answer of the routes it calls with ``route``; a request to any other route is answered 404 ``NOT_FOUND``. Every request is
recorded, with its headers and body, so that a test can assert what the client sent.
"""

import asyncio
import datetime
import ipaddress
import ssl
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@dataclass(frozen=True)
class Authority:
	"""A test certificate authority, and the files of the certificates it signs."""

	folder: Path
	key: ec.EllipticCurvePrivateKey
	certificate: x509.Certificate

	@classmethod
	def create(cls, folder: Path, name: str) -> "Authority":
		folder.mkdir(parents=True)
		key = ec.generate_private_key(ec.SECP256R1())
		subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
		certificate = (
			_builder(subject, subject, key)
			.add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
			.add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
			.sign(key, hashes.SHA256())
		)
		authority = cls(folder, key, certificate)
		(folder / "ca.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
		return authority

	@property
	def ca_file(self) -> Path:
		return self.folder / "ca.pem"

	def issue(self, name: str, usage: x509.ObjectIdentifier, *, address: str | None = None) -> tuple[Path, Path]:
		"""Signs a certificate for one usage, server or client authentication, and gives the paths of its certificate and key."""
		key = ec.generate_private_key(ec.SECP256R1())
		subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
		builder = _builder(subject, self.certificate.subject, key).add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
		if address is not None:
			builder = builder.add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(address))]), critical=False)
		certificate = builder.sign(self.key, hashes.SHA256())
		certificate_file = self.folder / f"{name}.pem"
		key_file = self.folder / f"{name}-key.pem"
		certificate_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
		key_file.write_bytes(
			key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
		)
		return certificate_file, key_file


def _builder(subject: x509.Name, issuer: x509.Name, key: ec.EllipticCurvePrivateKey) -> x509.CertificateBuilder:
	now = datetime.datetime.now(datetime.timezone.utc)
	return (
		x509.CertificateBuilder()
		.subject_name(subject)
		.issuer_name(issuer)
		.public_key(key.public_key())
		.serial_number(x509.random_serial_number())
		.not_valid_before(now - datetime.timedelta(minutes=5))
		.not_valid_after(now + datetime.timedelta(days=1))
	)


@dataclass(frozen=True)
class Recorded:
	"""One request the fake service received."""

	method: str
	path: str
	"""The path, with its escapes decoded."""

	query: dict[str, list[str]]
	headers: dict[str, str]
	body: bytes


@dataclass
class FakeService:
	"""The running fake service."""

	port: int
	routes: dict[tuple[str, str], Handler] = field(default_factory=dict)
	requests: list[Recorded] = field(default_factory=list)

	def route(self, method: str, path: str, handler: Handler) -> None:
		"""Answers requests with this method and path, without the query string, by the handler."""
		self.routes[(method, path)] = handler

	def requests_to(self, method: str, path: str) -> list[Recorded]:
		return [request for request in self.requests if request.method == method and request.path == path]

	async def dispatch(self, request: web.Request) -> web.StreamResponse:
		body = await request.read()
		query = {name: request.query.getall(name) for name in request.query}
		self.requests.append(Recorded(request.method, request.path, query, dict(request.headers), body))
		handler = self.routes.get((request.method, request.path))
		if handler is None:
			return web.json_response({"code": "NOT_FOUND", "message": f"No route {request.method} {request.path}."}, status=404)
		return await handler(request)


class FakeServiceThread:
	"""Runs a fake service on its own thread until stopped."""

	def __init__(self, server_tls: ssl.SSLContext) -> None:
		self._tls = server_tls
		self._loop = asyncio.new_event_loop()
		self._started = threading.Event()
		self._thread = threading.Thread(target=self._run, daemon=True)
		self._runner: web.AppRunner | None = None
		self.service: FakeService | None = None

	def start(self) -> FakeService:
		self._thread.start()
		self._started.wait()
		assert self.service is not None
		return self.service

	def stop(self) -> None:
		if self._runner is not None:
			asyncio.run_coroutine_threadsafe(self._runner.cleanup(), self._loop).result()
		self._loop.call_soon_threadsafe(self._loop.stop)
		self._thread.join()
		self._loop.close()

	def _run(self) -> None:
		asyncio.set_event_loop(self._loop)
		self._loop.run_until_complete(self._serve())
		self._started.set()
		self._loop.run_forever()

	async def _serve(self) -> None:
		holder: list[FakeService] = []

		async def dispatch(request: web.Request) -> web.StreamResponse:
			return await holder[0].dispatch(request)

		app = web.Application(client_max_size=64 * 1024 * 1024)
		app.router.add_route("*", "/{tail:.*}", dispatch)
		self._runner = web.AppRunner(app, handle_signals=False)
		await self._runner.setup()
		site = web.TCPSite(self._runner, "127.0.0.1", 0, ssl_context=self._tls)
		await site.start()
		server = site._server
		assert server is not None
		port = server.sockets[0].getsockname()[1]  # type: ignore[attr-defined]
		holder.append(FakeService(port))
		self.service = holder[0]


def server_tls(authority: Authority, certificate: Path, key: Path) -> ssl.SSLContext:
	"""The fake service's TLS: its own certificate, and only client certificates the authority signed."""
	context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
	context.load_cert_chain(certificate, key)
	context.load_verify_locations(authority.ca_file)
	context.verify_mode = ssl.CERT_REQUIRED
	return context


__all__ = ["Authority", "ExtendedKeyUsageOID", "FakeService", "FakeServiceThread", "Recorded", "server_tls"]
