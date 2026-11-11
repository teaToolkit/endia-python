"""The client of the EastIndia API, from which a caller reaches every route group."""

from __future__ import annotations

import os
from collections.abc import Mapping
from types import TracebackType

from . import _wire
from ._connection import Connection, ConnectionSettings, tls_context
from ._environment import choose, parse_tcp_address
from .archives import ArchivesClient
from .bases import BasesClient
from .desktop import DesktopClient
from .execs import ExecsClient
from .fleets import FleetsClient
from .images import ImagesClient
from .machines import MachinesClient
from .operations import OperationsClient
from .processes import ProcessesClient
from .stats import StatsClient
from .system import SystemClient, SystemInfo, VersionInfo
from .volumes import VolumesClient

DEFAULT_REQUEST_TIMEOUT = 100.0
"""The seconds a request may take to connect, and to deliver each read of its answer, where a time limit applies."""

DEFAULT_REQUEST_RETRIES = 2
"""How many times a changing request is sent again after its connection failed before any reply, as the CLI's default."""

DEFAULT_RETRY_DELAY = 1.0
"""The seconds between those attempts, as the CLI's default."""


class EastIndiaClient:
	"""A client of one EastIndia service, as one caller.

	Every request goes over mutual TLS: the client presents the caller's certificate, and accepts the server only when its
	certificate chains to EastIndia's own authority, ``ca.pem``, and names the address. The route groups are attributes:
	``images``, ``bases``, ``operations``, ``fleets``, ``machines``, ``volumes``, ``execs``, ``archives``, ``processes``,
	``desktop``, ``stats`` and ``system``. A client is used from one thread at a time; close it, or use it in a ``with`` block,
	to close its connections.
	"""

	def __init__(
		self,
		host: str,
		*,
		ca_file: str | os.PathLike[str],
		cert_file: str | os.PathLike[str],
		key_file: str | os.PathLike[str],
		request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
		request_retries: int = DEFAULT_REQUEST_RETRIES,
		retry_delay: float = DEFAULT_RETRY_DELAY,
	) -> None:
		"""Connects to a service.

		Args:
			host: The service's address, ``tcp://<host>:<port>`` as the CLI writes it, or ``https://<host>:<port>``.
			ca_file: EastIndia's authority, ``ca.pem``, which the server's certificate must chain to.
			cert_file: The caller's client certificate, ``cert.pem``.
			key_file: The client certificate's unencrypted private key, ``key.pem``.
			request_timeout: The seconds a request may take to connect, and to deliver each read of its answer. It does not apply to
				uploads, streamed answers, waits that the service answers only when they end, and requests sent once.
			request_retries: How many times a changing request is sent again, with the same ``Idempotency-Key``, after its
				connection failed before any reply.
			retry_delay: The seconds between those attempts.

		Raises:
			ValueError: The address is not over TCP.
			FileNotFoundError: A certificate file does not exist.
			ssl.SSLError: A file does not hold a certificate, or the key does not belong to the certificate.
		"""
		name, port = parse_tcp_address(host)
		settings = ConnectionSettings(
			host=name,
			port=port,
			tls=tls_context(os.fspath(ca_file), os.fspath(cert_file), os.fspath(key_file)),
			request_timeout=request_timeout,
			request_retries=request_retries,
			retry_delay=retry_delay,
		)
		self._connection = Connection(settings)
		self.operations = OperationsClient(self._connection)
		"""Operations: list, inspect, logs, cancel and wait."""
		self.images = ImagesClient(self._connection, self.operations)
		"""Images: build, list, inspect, tag and remove."""
		self.bases = BasesClient(self._connection, self.operations)
		"""Bases: import a disk and prepare from a Windows installation disc."""
		self.fleets = FleetsClient(self._connection, self.operations)
		"""Fleets: up and its dry run, down, stop, start, restart, list, inspect and logs."""
		self.machines = MachinesClient(self._connection, self.operations)
		"""Machines: list, inspect, logs, start, stop and restart."""
		self.volumes = VolumesClient(self._connection)
		"""Named volumes: list, inspect and remove."""
		self.execs = ExecsClient(self._connection)
		"""Programs run in a machine, attached or detached."""
		self.archives = ArchivesClient(self._connection)
		"""Copies into and out of a machine."""
		self.processes = ProcessesClient(self._connection)
		"""A machine's processes: list, inspect, wait, stop and logs."""
		self.desktop = DesktopClient(self._connection)
		"""A machine's desktop: launch, screenshot, inspect, wait, invoke, click, set values, keys, pointer, windows, tray and
		elevation prompts."""
		self.stats = StatsClient(self._connection)
		"""Readings of a machine or the host."""
		self.system = SystemClient(self._connection, self.operations)
		"""Disk usage and prune."""

	@classmethod
	def from_env(cls, environment: Mapping[str, str] | None = None, *, request_timeout: float = DEFAULT_REQUEST_TIMEOUT) -> EastIndiaClient:
		"""Connects where the ``endia`` CLI would, as ``docker.from_env()`` connects where ``docker`` would.

		``EASTINDIA_HOST`` names the address, with the certificate files in the folder ``EASTINDIA_CERT_PATH`` names. Without it,
		the CLI's context is used: ``EASTINDIA_CONTEXT``, else the current context of the CLI's configuration folder, else the
		built-in context ``default``. The configuration folder is ``EASTINDIA_CONFIG``, else ``.eastindia`` in the user's home
		folder; its ``config.json`` also gives the retries.

		Args:
			environment: The variables to read; None reads the process's own.

		Raises:
			ValueError: The configuration is invalid, the context does not exist, or it connects over a named pipe.
			FileNotFoundError: A certificate file does not exist.
		"""
		target = choose(os.environ if environment is None else environment)
		return cls(
			f"tcp://{target.host}:{target.port}",
			ca_file=target.ca_file,
			cert_file=target.cert_file,
			key_file=target.key_file,
			request_timeout=request_timeout,
			request_retries=target.request_retries,
			retry_delay=target.retry_delay,
		)

	@property
	def address(self) -> str:
		"""The service's host and port."""
		return self._connection.settings.address

	def version(self) -> VersionInfo:
		"""Reads the service's version: ``endia version``."""
		return _wire.read(self._connection.read("/v1/version"), VersionInfo)

	def info(self) -> SystemInfo:
		"""Reads the host's description, the configuration in effect and what needs attention: ``endia info``."""
		return _wire.read(self._connection.read("/v1/info"), SystemInfo)

	def close(self) -> None:
		"""Closes the client's connections."""
		self._connection.close()

	def __enter__(self) -> EastIndiaClient:
		return self

	def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None) -> None:
		self.close()
