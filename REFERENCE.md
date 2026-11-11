# endia

`endia` is the Python client of EastIndia, the Windows service that runs Hyper-V Windows virtual machines ("machines") for tests and is driven as Docker drives containers. It calls EastIndia's HTTPS API over mutual TLS and gives each answer as a typed record, so a test runner written in Python drives EastIndia without the `endia` CLI: a refusal arrives as an exception carrying the API's error code, and a program's exit code arrives in full. The import package is `endia`; it needs Python 3.10 or later, and its API is synchronous.

## How to use it

Install it from PyPI:

```
pip install endia
```

**Connect.** `EastIndiaClient.from_env()` connects where the `endia` CLI would: to `EASTINDIA_HOST` with the certificate files in `EASTINDIA_CERT_PATH`, or else through the CLI's current context in `~/.eastindia`. A client can also be given its address and the three files directly.

```python
from endia import EastIndiaClient

with EastIndiaClient.from_env() as client:
	print(client.version().version)

client = EastIndiaClient(
	"tcp://host.docker.internal:7443",
	ca_file="/run/eastindia/ca.pem",
	cert_file="/run/eastindia/cert.pem",
	key_file="/run/eastindia/key.pem",
)
```

**Images.** A build packs a context folder as `endia build` packs it, and starts an operation; `result()` waits for it and gives the image it made.

```python
built = client.images.build("lab-image", tags=["lab/dotnet:10"], build_args={"DOTNET_VERSION": "10.0.3"}).result()
print(built.digest, client.images.inspect("lab/dotnet:10").chain)
```

**Stages.** A recipe can hold several stages, each beginning with a `FROM` and named by its `AS`; a later stage can be built `FROM` an earlier one. A build makes the last stage, or the stage `target` names, as `endia build --target` does, and the tags name that stage's result. The stages below the target are kept as build cache: they are not images, but a later build whose stage has the same build key reuses them instead of building again. The result's `stages` lists each stage the build made or found, from the lowest to the target, with its `name`, `digest`, `build_key` and `cache_hit`.

```dockerfile
FROM eastindia/development:latest AS tools
RUN choco install -y git

FROM tools AS app
COPY publish/ C:/app/
```

```python
tools = client.images.build("lab-image", target="tools", tags=["lab/tools:1"]).result()   # builds tools alone
app = client.images.build("lab-image", tags=["lab/app:1"]).result()   # finds tools in the cache and builds app
print([(stage.name, stage.cache_hit) for stage in app.stages])   # [('tools', True), ('app', False)]
```

`images.prune_build_cache()` deletes the build cache that nothing uses, as `endia builder prune` does; its result lists the entries deleted, by digest, in `deleted_build_cache`, and the space freed in `reclaimed_bytes`. `system.prune()` deletes the same entries and lists them in `deleted_build_cache` too, and `system.disk_usage().build_cache` lists every entry with its size and whether a prune would delete it.

**Fleets.** `fleets.up` takes the project's resolved model, which `endia compose config --format json` prints. The SDK reads no Compose file itself; it sends the model with each building service's context and each bind mount's source, as `endia compose up` sends them. A model written as a mapping in the same form, with every path absolute, works too.

`result()` returns once every machine the operation created, recreated or started is ready: Windows has booted, the machine is configured, and when its image signs a user in by itself, that user is signed in with the desktop helper running. A test can drive the desktop as soon as `result()` returns, with no polling of its own. `fleets.wait` then does what `endia compose up --wait` adds: it waits for health checks to pass, checks machines that were kept running as well, and raises `MachineFailedError` when a machine exits or becomes unhealthy. `machines.inspect` gives `ready` too, with the same meaning as in `machines.list`.

```python
import json, subprocess

model = json.loads(subprocess.run(["endia", "compose", "-f", "classroom.yaml", "config", "--format", "json"],
	capture_output=True, text=True, check=True).stdout)
up = client.fleets.up(model).result(timeout=1800)
print([(machine.name, machine.action) for machine in up.machines])
client.fleets.wait(model["name"], timeout=900)   # only needed for health checks: --wait and --wait-timeout
client.fleets.down(model["name"], volumes=True).result()
```

**Purposes.** A purpose says what a fleet or a machine is being used for: a one-line title with optional Markdown notes, its detail. `fleets.up(model, purpose="Nightly UI run", purpose_detail=notes)` sets the fleet's purpose as the fleet comes up, as `endia compose up --purpose` and `--purpose-file` do; `purpose_detail` is the notes' text, not a file name, and it needs `purpose`. `fleets.purpose`, `set_purpose` and `clear_purpose` read, replace and remove a fleet's purpose, and `machines` has the same three for a machine's own purpose, which overrides its fleet's. `fleets.list`, `fleets.inspect`, `machines.list` and `machines.inspect` carry the `purpose`, and the machine records carry `purpose_source`, `machine` or `fleet`, where the purpose in effect comes from. A title with a line break, or a detail over 65536 bytes in UTF-8, raises `ValueError` before anything is sent; the service checks the rest.

**Running programs.** `execs.run` runs a program attached and gives its exit code, beyond 255, with everything it wrote to standard output and standard error. `execs.run_detached` starts it and returns its process ID, which `processes.wait` waits on.

```python
result = client.execs.run("classroom-server-1", ["cmd", "/c", "exit 3010"])
assert result.exit_code == 3010

started = client.execs.run_detached("classroom-server-1", ["powershell.exe", "-File", "C:\\tools\\seed.ps1"])
exit_code = client.processes.wait("classroom-server-1", started.process_id, timeout=600, started=started.started)
```

**Copying.** `archives.copy_in` and `archives.copy_out` follow `docker cp`'s rules for where a copy lands.

```python
client.archives.copy_in("classroom-server-1", "assets/Server.msi", "C:/setup/Server.msi")
client.archives.copy_out("classroom-server-1", "C:/results/.", "evidence/server")
```

**The desktop.** A program started on the desktop is named by its process ID and start time; its elements are named by selectors such as `Window[Sign in]/Edit#UserName`.

```python
app = client.desktop.launch("classroom-trainee-1", "C:\\Program Files\\Ledger\\Ledger.exe")
client.desktop.wait("classroom-trainee-1", app.process_id, "Window[Sign in]/Edit#UserName", timeout=120, started=app.started)
client.desktop.set_value("classroom-trainee-1", app.process_id, "Window[Sign in]/Edit#UserName", "trainee", started=app.started)
client.desktop.set_secret("classroom-trainee-1", app.process_id, "Window[Sign in]/Edit#Password", password, started=app.started)
client.desktop.invoke("classroom-trainee-1", app.process_id, "Window[Sign in]/Button[Sign in]", started=app.started)
png = client.desktop.screenshot("classroom-trainee-1").png
```

**Operations.** A route that starts an operation gives an `OperationHandle`, which follows the operation's log, waits for its end, cancels it, and reads its result.

```python
build = client.images.build("lab-image", tags=["lab/dotnet:10"])
for line in build.logs():
	print(line)
print(build.wait().state)
```

**Errors.** Every call raises one of two exceptions when it fails. `EastIndiaApiError` means the service answered and refused; its `code` is the API's error code. `EastIndiaConnectionError` means no answer came; its `failure` says why. An operation that ends without succeeding raises `OperationFailedError` from `result()`. All three derive from `EastIndiaError`.

```python
from endia import ConnectionFailure, ErrorCode, EastIndiaApiError, EastIndiaConnectionError

try:
	client.images.inspect("lab/dotnet:10")
except EastIndiaApiError as refused:
	if refused.code == ErrorCode.NOT_FOUND:
		...
	print(refused.status_code, refused.error.message, refused.error.field_path)
except EastIndiaConnectionError as failed:
	print(failed.failure)   # UNREACHABLE, CERTIFICATE_REFUSED, SERVER_NOT_TRUSTED or TIMED_OUT
```

### A test runner's steps with the SDK

A test runner that drives EastIndia through the CLI runs a PowerShell script in a machine with `endia exec`, waits for a desktop element with `endia desktop wait`, and types a password with `endia desktop set-value --secret-stdin`. It must recognise EastIndia's own failures by the text `Error response from EastIndia:` and tell them from the script's exit code, and on Linux it receives that exit code modulo 256. Through the SDK the same steps look like this:

```python
import base64
from endia import EastIndiaClient, EastIndiaApiError

TEST_IMAGE = "example/app-test:latest"
INSTRUCTOR = "Window"

def powershell(client, machine, script):
	"""Runs a PowerShell script in the machine and gives its exit code and output; a EastIndia refusal raises instead."""
	encoded = base64.b64encode(("$ErrorActionPreference='Stop'; " + script).encode("utf-16le")).decode()
	return client.execs.run(machine, ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded])

with EastIndiaClient.from_env() as client:
	model = {
		"name": "login-test-3f9a1c2b7d",
		"services": {"lab": {"image": TEST_IMAGE, "cpus": 2, "mem_limit": "4g", "networks": ["lab"]}},
		"networks": {"lab": {"internal": True}},
	}
	client.fleets.up(model, build="never").result(timeout=900)
	machine = "login-test-3f9a1c2b7d-lab-1"
	try:
		result = powershell(client, machine, "& C:\\Tests\\Set-Up.ps1")
		if result.exit_code != 0:
			raise AssertionError(f"Set-Up.ps1 exited {result.exit_code}: {result.stderr.decode(errors='replace')}")

		app = client.desktop.launch(machine, "powershell.exe", ["-NoProfile", "-File", "C:\\Tests\\Start-App.ps1"])
		client.desktop.wait(machine, app.process_id, INSTRUCTOR + "/Edit[Username]", timeout=120, started=app.started)
		client.desktop.set_value(machine, app.process_id, INSTRUCTOR + "/Edit[Username]", username, started=app.started)
		client.desktop.set_secret(machine, app.process_id, INSTRUCTOR + "/Edit[Password]", password, started=app.started)
		client.desktop.invoke(machine, app.process_id, INSTRUCTOR + "/Button[Sign in]", started=app.started)
	except EastIndiaApiError as refused:
		raise AssertionError(f"EastIndia refused: {refused.code}") from refused
	finally:
		client.fleets.down(model["name"], volumes=True, timeout=30).result()
```

`result.exit_code` is the script's own exit code, whatever its size. A failure of EastIndia itself is never mistaken for the script's exit code: the call raises `EastIndiaApiError`, whose `code` names the failure, such as `MACHINE_NOT_RUNNING`. The secret goes to the service once, in a request the service neither logs nor stores, and never appears in a command line or a transcript. A wait that runs out of time raises `EastIndiaApiError` with the code `TIMEOUT` and the window's last UI tree in `error.details["tree"]`.

## Folders

| Folder or file | What it holds |
|---|---|
| `src/endia/` | the package: `client.py` (`EastIndiaClient`), one module per area with its records and route group (`images`, `bases`, `operations`, `fleets`, `machines`, `volumes`, `execs`, `archives`, `processes`, `desktop`, `stats`, `system`), and `errors.py`. Modules whose names start with `_` are internal: the connection, the JSON conventions, the query string, the ignore-file rules, the tar archives, the fleet archive and the choice of connection from the environment |
| `tests/` | unit tests against `fake_service.py`, a real HTTPS server with mutual TLS whose routes each test supplies; `test_live.py`, which runs against the real service only when `EASTINDIA_LIVE=1` |
| `pyproject.toml` | the package, its dependencies, and the settings of pytest, mypy and ruff |

## What it does not do

* It has no asynchronous client: test runners are mostly synchronous.
* It does not reach the named pipe. `from_env` refuses a context on the pipe, which is the built-in context `default` on Windows.
* It has no client-certificate routes (`endia context export`, `cert ls`, `cert rm`): the service answers them only on the named pipe.
* It reads no Compose file: `fleets.up` takes the resolved model.
* A copy never follows a symbolic link, as `endia cp` does without `-L`.
* A client is used from one thread at a time.

## Reference

* **Server trust.** The server's certificate must chain to the authority in `ca_file`, be meant for server authentication and name the address the client connects to. The operating system's trust store is never consulted, and verification cannot be turned off.
* **Retries.** A request that changes something carries a new `Idempotency-Key` of 32 hexadecimal digits. When the request's connection fails before any reply, the client sends the request again with the same key, up to `request_retries` times (2 by default) and `retry_delay` seconds apart (1 by default). The service recognises the key and does the work once. A read is not sent again.
* **Requests sent once.** `bases.prepare`, `desktop.answer_prompt` and `desktop.set_secret` send their request once, with no `Idempotency-Key`, no repeat after a failed connection and no time limit of the client's own, because each carries a password or a secret. `desktop.screenshot` carries no key either, because it changes nothing.
* **Time limit.** `request_timeout` (100 seconds by default) limits connecting and each read of an answer. It does not apply to an upload (a build's context, a fleet's archive or a copy into a machine), a streamed answer such as a followed log, `processes.wait` and `processes.stop`, `desktop.wait`, and the requests sent once. A request that runs out of time raises `EastIndiaConnectionError` with `TIMED_OUT`, and is not sent again.
* **`from_env`.** It takes the first of these that is set: `EASTINDIA_HOST`, with the certificate files `ca.pem`, `cert.pem` and `key.pem` in `EASTINDIA_CERT_PATH`, else in the configuration folder; `EASTINDIA_CONTEXT`; `currentContext` in the configuration folder's `config.json`; and the built-in context `default`, which on Linux is `tcp://127.0.0.1:7443` with the files in the configuration folder. The configuration folder is `EASTINDIA_CONFIG`, else `~/.eastindia`. A stored context is read where the CLI writes it: `contexts/meta/<SHA-256 of the name>/meta.json` and `contexts/tls/<SHA-256 of the name>/eastindia/`. `config.json`'s `requestRetries` (0 to 10) and `requestRetryDelayMilliseconds` (at least 100) set the retries; an unknown key or a value out of range raises `ValueError`.
* **Operations.** `OperationHandle.wait` and `result` read the operation's state once a second. A `timeout` that passes raises `TimeoutError`, and the operation carries on.
* **Fleet wait.** `fleets.wait` reads the fleet once a second, as `endia compose up --wait` does. It waits for the machines of the named services and of every service they depend on, or of every service when none is named. A `timeout` that passes raises `TimeoutError`, and the machines carry on.
* **Exec.** Attached, a WebSocket carries the program's output in Docker's multiplexed frames, which `ExecSession` gives as `(StreamKind, bytes)` pairs. The exec's state then gives the exit code: 127 for a program that was not found and 126 for one that could not start, with the reason in `start_error`; None when a restart of the machine ended the program.
* **Times.** A time the service wrote is a `Timestamp`, an aware `datetime` in UTC that remembers the service's text. The service writes times to 100 nanoseconds, finer than a `datetime`, so a process's `started` time sent back with a `Timestamp` names exactly the process the service named.
* **Build contexts.** `images.build` sends every file, folder and link of the context folder except those the ignore file leaves out. Without `dockerfile` the recipe is `Endiafile` in the folder when it exists, and `Dockerfile` otherwise. The ignore file is the first that exists of the recipe's own `.endiaignore` and `.dockerignore`, each named as the recipe followed by that name, and then `.endiaignore` and `.dockerignore` at the folder's root. A fleet's build contexts follow the same rules. The recipe and the ignore file are always sent, and a link is sent as a link. A recipe outside the folder is added at the archive's root as `.dockerfile.` followed by 16 random hexadecimal digits. `images.build_archive` sends a tar archive the caller has packed.
* **Fleet archive.** `fleets.up` sends `compose.json`, the model; `purpose.json`, the title and detail, when a purpose is given; `contexts/<service>/`, the context of each selected service that builds; and `binds/<service>/<index>`, the source of each bind mount, by its index in the service's `volumes`. A build or volume in short syntax, or a path that is not absolute, raises `ValueError`. The fleet's name is the model's `name`.
* **Copies.** A path in a machine is an absolute Windows path, with `\` or `/`, or a path relative to `C:\`. A file is saved under the destination's name, or into the destination when it is an existing folder; a folder becomes the destination when that does not exist, or goes into an existing folder as a subfolder; a folder whose path ends in `/.` has its contents copied.
* **Version.** The package has its own version, `version` in `pyproject.toml`, separate from the EastIndia service's. Multi-stage builds (`target`, `stages`) and `prune_build_cache` need EastIndia 0.16.82 or later.
* **Packages.** httpx (BSD-3-Clause) sends the requests, with httpcore (BSD-3-Clause), h11 (MIT), anyio (MIT), idna (BSD-3-Clause) and certifi (MPL-2.0), whose certificates the SDK never uses. websockets 15 or later (BSD-3-Clause) carries the exec WebSocket. cattrs 24.1 or later (MIT), with attrs (MIT), reads the records. The tests use pytest (MIT), aiohttp (Apache-2.0 AND MIT) and cryptography (Apache-2.0 OR BSD-3-Clause); mypy (MIT) and ruff (MIT) check the code, and hatchling (MIT) builds the package.
* **Tests.** In this folder, with a virtual environment in `.venv`:

  ```
  uv venv .venv && uv pip install --python .venv/bin/python -e . --group test --group lint
  .venv/bin/pytest
  .venv/bin/mypy && .venv/bin/ruff check && .venv/bin/ruff format --check
  EASTINDIA_LIVE=1 .venv/bin/pytest tests/test_live.py
  ```

  The live tests touch only what they create, named with the prefix `sdk-python-`: one image built over `eastindia/development:latest` and removed again, and one fleet of one machine from that image with `mem_limit: 4g`, removed whether the tests pass or fail.
