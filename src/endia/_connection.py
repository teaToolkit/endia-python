"""The one connection that every route group of a client shares.

Over HTTPS it presents the caller's client certificate and accepts the server only through EastIndia's own authority. A request
that changes something carries an ``Idempotency-Key`` and is sent again with the same key when its connection fails before any
reply. This module is also the one place where a refusal becomes ``EastIndiaApiError`` and a failure to connect becomes
``EastIndiaConnectionError``.
"""

import base64
import binascii
import json
import ssl
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from typing import IO, Any

import httpx
from websockets.exceptions import InvalidHandshake, InvalidStatus
from websockets.sync.client import ClientConnection
from websockets.sync.client import connect as connect_websocket

from . import _wire
from .errors import ApiError, ConnectionFailure, EastIndiaApiError, EastIndiaConnectionError

IDEMPOTENCY_HEADER = "Idempotency-Key"
ERROR_HEADER = "X-EastIndia-Error"
"""The header that carries, as Base64 of its JSON, the error of a refusal whose answer has no body, such as a HEAD's."""

JSON_MEDIA_TYPE = "application/json"
TAR_MEDIA_TYPE = "application/x-tar"

# An upload's body is read from the caller's stream, and sent, one piece of this many bytes at a time.
_UPLOAD_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, kw_only=True)
class ConnectionSettings:
	"""Where the service is, how it is reached, and how patiently."""

	host: str
	"""The service's host name or address, which its certificate must name."""

	port: int
	"""The service's TCP port."""

	tls: ssl.SSLContext
	"""The TLS settings: the client certificate, and the authority that the server's certificate must chain to."""

	request_timeout: float
	"""The seconds allowed to connect, and to wait for each read of an answer, where a time limit applies."""

	request_retries: int
	"""How many times a changing request is sent again after its connection failed before any reply."""

	retry_delay: float
	"""The seconds between those attempts."""

	@property
	def address(self) -> str:
		"""The host and port, as a URL and messages write them, with an IPv6 address in brackets."""
		return f"[{self.host}]:{self.port}" if ":" in self.host else f"{self.host}:{self.port}"


def tls_context(ca_file: str, cert_file: str, key_file: str) -> ssl.SSLContext:
	"""Builds the TLS settings of mutual TLS with EastIndia.

	The server must present a certificate that chains to the authority in ``ca_file``, is meant for server authentication and
	names the address the client connects to. The operating system's trust store is never consulted, and verification cannot be
	turned off. The client presents the certificate in ``cert_file`` with its unencrypted key in ``key_file``.

	Raises:
		FileNotFoundError: One of the three files does not exist.
		ssl.SSLError: A file does not hold a certificate, or the key does not belong to the certificate.
	"""
	context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
	context.minimum_version = ssl.TLSVersion.TLSv1_2
	context.load_verify_locations(cafile=ca_file)
	context.load_cert_chain(certfile=cert_file, keyfile=key_file)
	return context


def classify(error: BaseException) -> ConnectionFailure:
	"""Gives the reason no answer came, from the exception that sending the request raised.

	The exception and its causes are examined from the outermost inwards. A failed check of the server's certificate is
	``SERVER_NOT_TRUSTED``. Any other TLS failure is ``CERTIFICATE_REFUSED``: the service refuses a client certificate with a TLS
	alert, which under TLS 1.2 ends the handshake and under TLS 1.3 arrives on the first read. Anything else is ``UNREACHABLE``.
	"""
	cause: BaseException | None = error
	while cause is not None:
		if isinstance(cause, ssl.SSLCertVerificationError):
			return ConnectionFailure.SERVER_NOT_TRUSTED
		if isinstance(cause, ssl.SSLError) or "alert" in str(cause).lower():
			return ConnectionFailure.CERTIFICATE_REFUSED
		cause = cause.__cause__ or cause.__context__
	return ConnectionFailure.UNREACHABLE


def read_error(body: bytes | bytearray, header: str | None) -> ApiError | None:
	"""Reads a refusal's error from its body, or from the ``X-EastIndia-Error`` header when the body holds none."""
	try:
		return _wire.read(json.loads(body), ApiError)
	except ValueError:
		pass
	try:
		return _wire.read(json.loads(base64.b64decode(header or "", validate=True)), ApiError)
	except (ValueError, binascii.Error):
		return None


class Connection:
	"""Sends the requests of every route group, and turns every failure into one of the SDK's two exceptions."""

	def __init__(self, settings: ConnectionSettings, transport: httpx.BaseTransport | None = None) -> None:
		self.settings = settings
		self._http = httpx.Client(
			base_url=f"https://{settings.address}",
			verify=settings.tls,
			timeout=settings.request_timeout,
			transport=transport,
		)

	def close(self) -> None:
		"""Closes the connection's sockets."""
		self._http.close()

	def read(self, path: str) -> Any:
		"""Sends a ``GET`` and gives its parsed JSON answer. A read is never sent again."""
		return self._send(self._http.build_request("GET", path), timed=True).json()

	def head(self, path: str) -> httpx.Headers:
		"""Sends a ``HEAD`` and gives the answer's headers; a refusal carries its error in the ``X-EastIndia-Error`` header."""
		return self._send(self._http.build_request("HEAD", path), timed=True).headers

	def change(
		self,
		method: str,
		path: str,
		*,
		json_body: Any = None,
		archive: IO[bytes] | None = None,
		timed: bool = True,
	) -> httpx.Response:
		"""Sends a request that changes something, and gives the answer with its body read.

		The request carries a new ``Idempotency-Key``. When its connection fails before any reply, it is sent again with the same key
		up to ``request_retries`` times, ``retry_delay`` seconds apart, so that the service does the work once. An ``archive``, the
		body of an upload, must be seekable: each attempt sends it again from where it started.

		Args:
			timed: Whether the request time-out applies. It does not for an upload, whose body can take longer to send, nor for a
				wait that the service answers only when the wait ends.
		"""
		key = uuid.uuid4().hex
		start = archive.tell() if archive is not None else 0
		attempt = 0
		while True:
			request = self._build(method, path, json_body, archive, start)
			request.headers[IDEMPOTENCY_HEADER] = key
			try:
				return self._send(request, timed=timed)
			except EastIndiaConnectionError as failed:
				if failed.failure is not ConnectionFailure.UNREACHABLE or attempt >= self.settings.request_retries:
					raise
			attempt += 1
			time.sleep(self.settings.retry_delay)

	def once(self, path: str, json_body: Any) -> httpx.Response:
		"""Sends a ``POST`` exactly once, without an ``Idempotency-Key`` and without a time limit.

		A request whose body carries a password or a secret goes this way: the service stores no reply to it, and a repeat could
		answer an elevation prompt twice.
		"""
		return self._send(self._build("POST", path, json_body, None, 0), timed=False)

	def unkeyed(self, path: str, json_body: Any) -> httpx.Response:
		"""Sends a ``POST`` that changes nothing, such as a screenshot, without an ``Idempotency-Key``, and gives the whole answer."""
		return self._send(self._build("POST", path, json_body, None, 0), timed=True)

	def open_stream(self, path: str) -> httpx.Response:
		"""Sends a ``GET`` whose answer is read as it arrives, such as a followed log, with no time limit on the reading. The caller
		closes the answer.
		"""
		return self._send(self._http.build_request("GET", path), timed=False, stream=True)

	def lines(self, path: str) -> Iterator[str]:
		"""Reads a streamed answer line by line, until the service ends it."""
		response = self.open_stream(path)
		try:
			yield from response.iter_lines()
		except httpx.TransportError as failed:
			raise EastIndiaConnectionError(self.settings.address, classify(failed)) from failed
		finally:
			response.close()

	def websocket(self, path: str, owner: ExitStack) -> ClientConnection:
		"""Opens a route as a WebSocket, over the same mutual TLS as every other request. Closing ``owner`` closes it.

		Raises:
			EastIndiaApiError: The service refused the upgrade; the refusal's body says why.
			EastIndiaConnectionError: No answer came.
		"""
		try:
			opened = owner.enter_context(
				connect_websocket(
					f"wss://{self.settings.address}{path}",
					ssl=self.settings.tls,
					open_timeout=self.settings.request_timeout,
					max_size=None,
					# The client sends a ping every 20 seconds, so that a proxy does not close a connection whose program prints nothing
					# for a while. The client never closes the connection because a ping went unanswered, because the program may still
					# be running.
					ping_timeout=None,
				),
			)
		except InvalidStatus as refused:
			response = refused.response
			raise EastIndiaApiError(response.status_code, read_error(response.body or b"", response.headers.get(ERROR_HEADER))) from refused
		except (OSError, InvalidHandshake, TimeoutError) as failed:
			raise EastIndiaConnectionError(self.settings.address, classify(failed)) from failed
		# websockets 15 and 16 declare the entered connection as their base type; the synchronous client always enters a
		# ClientConnection.
		assert isinstance(opened, ClientConnection)
		return opened

	def _build(self, method: str, path: str, json_body: Any, archive: IO[bytes] | None, start: int) -> httpx.Request:
		if archive is not None:
			length = archive.seek(0, 2) - start
			headers = {"Content-Type": TAR_MEDIA_TYPE, "Content-Length": str(length)}
			return self._http.build_request(method, path, content=_from_start(archive, start), headers=headers, timeout=self._untimed())
		if json_body is not None:
			content = json.dumps(json_body, separators=(",", ":")).encode()
			return self._http.build_request(method, path, content=content, headers={"Content-Type": JSON_MEDIA_TYPE})
		return self._http.build_request(method, path)

	def _untimed(self) -> httpx.Timeout:
		return httpx.Timeout(None, connect=self.settings.request_timeout)

	def _send(self, request: httpx.Request, *, timed: bool, stream: bool = False) -> httpx.Response:
		if not timed:
			request.extensions["timeout"] = self._untimed().as_dict()
		try:
			response = self._http.send(request, stream=stream)
		except httpx.TimeoutException as failed:
			raise EastIndiaConnectionError(self.settings.address, ConnectionFailure.TIMED_OUT) from failed
		except httpx.TransportError as failed:
			raise EastIndiaConnectionError(self.settings.address, classify(failed)) from failed
		if response.is_success:
			return response
		body = response.read()
		response.close()
		raise EastIndiaApiError(response.status_code, read_error(body, response.headers.get(ERROR_HEADER)))


def _from_start(archive: IO[bytes], start: int) -> Iterator[bytes]:
	archive.seek(start)
	while chunk := archive.read(_UPLOAD_CHUNK_BYTES):
		yield chunk
