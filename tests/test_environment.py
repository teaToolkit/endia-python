"""Where ``from_env`` connects: the environment first, then the CLI's contexts, read in the layout the CLI writes them."""

import hashlib
import json
from pathlib import Path

import pytest

from endia._environment import choose, parse_tcp_address


def store_context(folder: Path, name: str, host: str) -> Path:
	"""Writes a context as ``endia context create`` writes it, and gives its certificate folder."""
	digest = hashlib.sha256(name.encode()).hexdigest()
	meta = folder / "contexts" / "meta" / digest
	meta.mkdir(parents=True)
	(meta / "meta.json").write_text(
		json.dumps({"Name": name, "Metadata": {"Description": None}, "Endpoints": {"eastindia": {"Host": host}}})
	)
	return folder / "contexts" / "tls" / digest / "eastindia"


# Examples of use


def test_current_context_of_the_config_folder_names_the_address_and_its_files(tmp_path: Path) -> None:
	certificates = store_context(tmp_path, "pc", "tcp://host.docker.internal:7443")
	(tmp_path / "config.json").write_text('{"currentContext": "pc", "requestRetries": 4, "requestRetryDelayMilliseconds": 250}')

	target = choose({"EASTINDIA_CONFIG": str(tmp_path)})

	assert (target.host, target.port) == ("host.docker.internal", 7443)
	assert target.ca_file == str(certificates / "ca.pem")
	assert (target.request_retries, target.retry_delay) == (4, 0.25)


def test_eastindia_host_wins_over_the_context_with_files_from_eastindia_cert_path(tmp_path: Path) -> None:
	store_context(tmp_path, "pc", "tcp://host.docker.internal:7443")
	(tmp_path / "config.json").write_text('{"currentContext": "pc"}')

	target = choose({"EASTINDIA_CONFIG": str(tmp_path), "EASTINDIA_HOST": "tcp://10.0.0.5:7443", "EASTINDIA_CERT_PATH": "/run/eastindia"})

	assert (target.host, target.cert_file) == ("10.0.0.5", "/run/eastindia/cert.pem")
	assert (target.request_retries, target.retry_delay) == (2, 1.0)


def test_eastindia_context_wins_over_the_current_context(tmp_path: Path) -> None:
	store_context(tmp_path, "pc", "tcp://host.docker.internal:7443")
	store_context(tmp_path, "lab", "tcp://192.168.1.20:7443")
	(tmp_path / "config.json").write_text('{"currentContext": "pc"}')

	assert choose({"EASTINDIA_CONFIG": str(tmp_path), "EASTINDIA_CONTEXT": "lab"}).host == "192.168.1.20"


def test_addresses_over_tcp_and_https_are_read_alike() -> None:
	assert parse_tcp_address("tcp://host.docker.internal:7443") == ("host.docker.internal", 7443)
	assert parse_tcp_address("https://[::1]:7443") == ("::1", 7443)


# Examples of misuse


def test_unknown_key_in_config_json_is_refused_naming_it(tmp_path: Path) -> None:
	(tmp_path / "config.json").write_text('{"requestRetrys": 3}')

	with pytest.raises(ValueError, match="requestRetrys"):
		choose({"EASTINDIA_CONFIG": str(tmp_path)})


def test_retries_out_of_range_are_refused(tmp_path: Path) -> None:
	(tmp_path / "config.json").write_text('{"requestRetries": 11}')

	with pytest.raises(ValueError, match="from 0 to 10"):
		choose({"EASTINDIA_CONFIG": str(tmp_path)})


def test_context_that_does_not_exist_is_refused(tmp_path: Path) -> None:
	with pytest.raises(ValueError, match='"lab" does not exist'):
		choose({"EASTINDIA_CONFIG": str(tmp_path), "EASTINDIA_CONTEXT": "lab"})


def test_named_pipe_address_is_refused_because_the_sdk_does_not_reach_it() -> None:
	with pytest.raises(ValueError, match="named pipe"):
		parse_tcp_address("npipe:////./pipe/eastindia_engine")
