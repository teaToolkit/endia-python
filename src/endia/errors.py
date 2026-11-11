"""The ways a call to EastIndia fails.

Every call raises one of two exceptions when it fails. ``EastIndiaApiError`` means the service answered and refused the request;
its ``code``, such as ``NOT_FOUND`` or ``TIMEOUT``, says why. ``EastIndiaConnectionError`` means no answer came. Both derive from
``EastIndiaError``. A long operation that ends in failure raises ``OperationFailedError`` when its result is asked for, and a
machine that exits or becomes unhealthy while ``FleetsClient.wait`` waits for it raises ``MachineFailedError``.
"""

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
	from .machines import MachineSummary
	from .operations import Operation


class ErrorCode(str, enum.Enum):
	"""The codes an ``ApiError`` carries. A member compares equal to its text, so ``error.code == ErrorCode.NOT_FOUND`` holds."""

	INVALID_REQUEST = "INVALID_REQUEST"
	NOT_FOUND = "NOT_FOUND"
	PERMISSION_DENIED = "PERMISSION_DENIED"
	INVALID_DEFINITION = "INVALID_DEFINITION"
	UNSUPPORTED_FIELD = "UNSUPPORTED_FIELD"
	UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
	CAPABILITY_MISSING = "CAPABILITY_MISSING"
	ACTION_UNSUPPORTED = "ACTION_UNSUPPORTED"
	ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
	ELEMENT_AMBIGUOUS = "ELEMENT_AMBIGUOUS"
	NAME_IN_USE = "NAME_IN_USE"
	RESOURCE_IN_USE = "RESOURCE_IN_USE"
	FLEET_CONFLICT = "FLEET_CONFLICT"
	NETWORK_CONFLICT = "NETWORK_CONFLICT"
	PORT_CONFLICT = "PORT_CONFLICT"
	MACHINE_NOT_RUNNING = "MACHINE_NOT_RUNNING"
	DESKTOP_UNAVAILABLE = "DESKTOP_UNAVAILABLE"
	FOCUS_REFUSED = "FOCUS_REFUSED"
	UNEXPECTED_PROMPT = "UNEXPECTED_PROMPT"
	FILES_LOCKED = "FILES_LOCKED"
	IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
	CAPACITY_REFUSED = "CAPACITY_REFUSED"
	STORAGE_FULL = "STORAGE_FULL"
	INTEGRITY_FAILED = "INTEGRITY_FAILED"
	GUEST_UNREACHABLE = "GUEST_UNREACHABLE"
	HYPERV_CONNECTION_FAILED = "HYPERV_CONNECTION_FAILED"
	HYPERV_BUSY = "HYPERV_BUSY"
	MACHINE_BUSY = "MACHINE_BUSY"
	HYPERV_REFUSED = "HYPERV_REFUSED"
	TIMEOUT = "TIMEOUT"
	BUILD_FAILED = "BUILD_FAILED"
	CANCELLED = "CANCELLED"
	INTERRUPTED = "INTERRUPTED"
	INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True, kw_only=True, slots=True)
class ApiError:
	"""Why the service refused a request, or why an operation failed.

	A caller decides what to do from ``code`` and never needs to parse ``message``.
	"""

	code: str
	"""The error code, one of ``ErrorCode``, such as ``NOT_FOUND``."""

	message: str
	"""What was expected and what was found, in one or two sentences. It never contains a password or a key."""

	field_path: str | None = None
	"""The field at fault: a request member such as ``reference``, a Compose path or a recipe position; None when none is."""

	details: dict[str, Any] | None = None
	"""Data that some codes carry, such as ``candidates`` for an ambiguous prefix or ``tree`` for a selector that matched nothing."""


class EastIndiaError(Exception):
	"""The base of every exception the SDK raises for a failed call."""


class EastIndiaApiError(EastIndiaError):
	"""The service answered and refused the request."""

	def __init__(self, status_code: int, error: ApiError | None) -> None:
		self.status_code = status_code
		"""The HTTP status the service answered with."""
		self.error = error
		"""The error the answer carried, or None when it carried none, as a 401 does."""
		super().__init__(_describe_refusal(status_code, error))

	@property
	def code(self) -> str | None:
		"""The error code, such as ``NOT_FOUND``, or None when the answer carried no error."""
		return self.error.code if self.error is not None else None


def _describe_refusal(status_code: int, error: ApiError | None) -> str:
	if error is None:
		return f"The service answered {status_code}."
	field = f" [{error.field_path}]" if error.field_path is not None else ""
	return f"The service answered {status_code} ({error.code}): {error.message}{field}"


class ConnectionFailure(enum.Enum):
	"""Why no answer came."""

	UNREACHABLE = "unreachable"
	"""Nothing answered at the address, or the connection broke before the answer arrived."""

	CERTIFICATE_REFUSED = "certificateRefused"
	"""The service refused the client certificate: it comes from another authority, has expired or was revoked."""

	SERVER_NOT_TRUSTED = "serverNotTrusted"
	"""The server's certificate does not chain to EastIndia's authority in ``ca.pem``, or does not name the address."""

	TIMED_OUT = "timedOut"
	"""The service did not answer within the client's request time-out."""


class EastIndiaConnectionError(EastIndiaError):
	"""No answer came from the service."""

	def __init__(self, address: str, failure: ConnectionFailure) -> None:
		self.address = address
		"""The service's host and port."""
		self.failure = failure
		"""Why no answer came."""
		super().__init__(_describe_failure(address, failure))


def _describe_failure(address: str, failure: ConnectionFailure) -> str:
	if failure is ConnectionFailure.CERTIFICATE_REFUSED:
		return f"The EastIndia service at {address} refused the client certificate."
	if failure is ConnectionFailure.SERVER_NOT_TRUSTED:
		return f"The server at {address} did not present a certificate from the EastIndia authority in ca.pem."
	if failure is ConnectionFailure.TIMED_OUT:
		return f"The EastIndia service at {address} did not answer in time."
	return f"Cannot connect to the EastIndia service at {address}. Is the service running?"


class OperationFailedError(EastIndiaError):
	"""An operation ended without succeeding: it failed, was cancelled, or was interrupted when the service stopped."""

	def __init__(self, operation: "Operation") -> None:
		self.operation = operation
		"""The operation as it ended."""
		super().__init__(_describe_operation(operation))

	@property
	def error(self) -> ApiError | None:
		"""Why the operation ended, such as ``BUILD_FAILED`` or ``CANCELLED``."""
		return self.operation.error

	@property
	def code(self) -> str | None:
		"""The error code of the operation's error, or None when it carries none."""
		return self.operation.error.code if self.operation.error is not None else None


def _describe_operation(operation: "Operation") -> str:
	reason = f" ({operation.error.code}): {operation.error.message}" if operation.error is not None else "."
	return f"The {operation.kind} operation {operation.id} on {operation.target} ended {operation.state}{reason}"


class MachineFailedError(EastIndiaError):
	"""A machine exited or became unhealthy while ``FleetsClient.wait`` waited for its fleet, as ``endia compose up --wait``
	then exits with 1.
	"""

	def __init__(self, machine: "MachineSummary") -> None:
		self.machine = machine
		"""The machine as the wait last read it."""
		state = f"exited ({machine.exit_code})" if machine.state == "exited" else "unhealthy"
		super().__init__(f"machine {machine.name} is {state}")


class ExecTimeoutError(EastIndiaError):
	"""A program that ``ExecsClient.run`` ran did not end within its ``timeout``. The client stopped it, with the processes it
	started, and gives what it wrote until then.
	"""

	def __init__(self, exec_id: str, timeout: float, stdout: bytes, stderr: bytes) -> None:
		self.exec_id = exec_id
		"""The exec's ID."""
		self.timeout = timeout
		"""The seconds the program was given."""
		self.stdout = stdout
		"""What the program wrote to its standard output before it was stopped."""
		self.stderr = stderr
		"""What the program wrote to its standard error before it was stopped."""
		super().__init__(f"exec {exec_id} did not end within {timeout:g} seconds, so it was stopped")
