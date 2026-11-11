"""Where ``EastIndiaClient.from_env`` connects, chosen as the ``endia`` CLI chooses it, without the CLI's own options.

The first of these that is set wins: ``EASTINDIA_HOST``, with the three certificate files in the folder ``EASTINDIA_CERT_PATH``
names, else in the configuration folder; ``EASTINDIA_CONTEXT``; ``currentContext`` in the configuration folder's
``config.json``; and the built-in context ``default``. The configuration folder is ``EASTINDIA_CONFIG``, else ``.eastindia`` in
the user's home folder.

The CLI keeps contexts in Docker's layout: ``contexts/meta/<SHA-256 of the name>/meta.json`` names the address, and
``contexts/tls/<SHA-256 of the name>/eastindia/`` holds ``ca.pem``, ``cert.pem`` and ``key.pem``. The built-in context ``default``
is not stored. On Linux it is ``tcp://127.0.0.1:7443`` with the certificate files in the configuration folder; on Windows it is the
service's named pipe.
"""

import hashlib
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HOST_VARIABLE = "EASTINDIA_HOST"
CERT_PATH_VARIABLE = "EASTINDIA_CERT_PATH"
CONTEXT_VARIABLE = "EASTINDIA_CONTEXT"
CONFIG_VARIABLE = "EASTINDIA_CONFIG"

DEFAULT_CONTEXT = "default"
_DEFAULT_TCP_HOST = "tcp://127.0.0.1:7443"
_DEFAULT_PIPE_HOST = "npipe:////./pipe/eastindia_engine"
_ENDPOINT_KEY = "eastindia"

# The CLI's settings in config.json: each key with its default and the range the CLI accepts.
_RETRIES_KEY = "requestRetries"
_RETRIES_RANGE = (0, 10)
_DELAY_KEY = "requestRetryDelayMilliseconds"
_DELAY_MINIMUM = 100
_KNOWN_KEYS = frozenset({"currentContext", _RETRIES_KEY, _DELAY_KEY})

_TCP_ADDRESS = re.compile(r"tcp://(?P<host>\[[^\]]+\]|[^:/\[\]]+):(?P<port>\d+)/?")


@dataclass(frozen=True, kw_only=True)
class Target:
	"""Where to connect and with which files, as the environment and the CLI's configuration choose it."""

	host: str
	port: int
	ca_file: str
	cert_file: str
	key_file: str
	request_retries: int
	retry_delay: float


def parse_tcp_address(address: str) -> tuple[str, int]:
	"""Reads an address of the form ``tcp://<host>:<port>``, or ``https://<host>:<port>``, into its host and port.

	Raises:
		ValueError: The address has another form, such as a named pipe's.
	"""
	text = "tcp://" + address.removeprefix("https://") if address.startswith("https://") else address
	match = _TCP_ADDRESS.fullmatch(text)
	if match is None:
		if address.startswith("npipe:"):
			raise ValueError(f"The address '{address}' is a named pipe, which this SDK does not reach; use a context or address over TCP.")
		raise ValueError(f"Expected an address of the form tcp://<host>:<port>; found '{address}'.")
	return match["host"].strip("[]"), int(match["port"])


def choose(environment: Mapping[str, str]) -> Target:
	"""Chooses where to connect from the environment and the CLI's configuration folder.

	Raises:
		ValueError: ``config.json`` holds an unknown key or a value out of range; the context named does not exist; or the
			address chosen is not over TCP.
	"""
	folder = Path(environment.get(CONFIG_VARIABLE) or Path.home() / ".eastindia")
	settings = _read_settings(folder / "config.json")
	retries, delay = _retries(settings, folder / "config.json")
	if (address := environment.get(HOST_VARIABLE)) is not None:
		certificates = Path(environment.get(CERT_PATH_VARIABLE) or folder)
	else:
		name = environment.get(CONTEXT_VARIABLE) or settings.get("currentContext") or DEFAULT_CONTEXT
		address, certificates = _context(folder, name)
	host, port = parse_tcp_address(address)
	return Target(
		host=host,
		port=port,
		ca_file=str(certificates / "ca.pem"),
		cert_file=str(certificates / "cert.pem"),
		key_file=str(certificates / "key.pem"),
		request_retries=retries,
		retry_delay=delay,
	)


def _context(folder: Path, name: str) -> tuple[str, Path]:
	if name == DEFAULT_CONTEXT:
		return (_DEFAULT_PIPE_HOST if sys.platform == "win32" else _DEFAULT_TCP_HOST), folder
	digest = hashlib.sha256(name.encode()).hexdigest()
	meta = folder / "contexts" / "meta" / digest / "meta.json"
	if not meta.is_file():
		raise ValueError(f'The context "{name}" does not exist in {folder}.')
	endpoint = json.loads(meta.read_text(encoding="utf-8")).get("Endpoints", {}).get(_ENDPOINT_KEY, {})
	if not isinstance(endpoint.get("Host"), str):
		raise ValueError(f"{meta} names no {_ENDPOINT_KEY} endpoint.")
	return endpoint["Host"], folder / "contexts" / "tls" / digest / _ENDPOINT_KEY


def _read_settings(path: Path) -> dict[str, Any]:
	if not path.is_file():
		return {}
	settings = json.loads(path.read_text(encoding="utf-8"))
	if not isinstance(settings, dict):
		raise ValueError(f"{path} does not hold a JSON object.")
	unknown = sorted(set(settings) - _KNOWN_KEYS)
	if unknown:
		raise ValueError(f"{path} holds the unknown key '{unknown[0]}'.")
	return settings


def _retries(settings: Mapping[str, Any], path: Path) -> tuple[int, float]:
	retries = settings.get(_RETRIES_KEY, 2)
	delay = settings.get(_DELAY_KEY, 1000)
	if type(retries) is not int or not _RETRIES_RANGE[0] <= retries <= _RETRIES_RANGE[1]:
		raise ValueError(f"{path}: {_RETRIES_KEY} must be an integer from 0 to 10; found {retries!r}.")
	if type(delay) is not int or delay < _DELAY_MINIMUM:
		raise ValueError(f"{path}: {_DELAY_KEY} must be an integer of at least 100; found {delay!r}.")
	return retries, delay / 1000
