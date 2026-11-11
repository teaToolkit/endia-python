"""Operations: the work that drives Hyper-V or waits on Windows, which takes seconds to hours and outlives the caller's connection.

A route that starts an operation answers at once with the operation's record. The SDK gives the caller an ``OperationHandle``
for it, which follows the operation's log, waits for its end, cancels it, and reads its result as a typed record.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, TypeVar

import httpx

from . import _wire
from ._connection import Connection
from ._query import Query, segment
from .errors import ApiError, OperationFailedError

T = TypeVar("T")

_OPERATIONS = "/v1/operations"

# A wait reads the operation's state once a second. An operation takes seconds to hours, so noticing its end up to a second late
# costs the caller nothing.
_POLL_SECONDS = 1.0


class OperationState:
	"""The states of an operation. An operation starts ``queued``, then runs; the last four states never change again."""

	QUEUED = "queued"
	RUNNING = "running"
	CANCELLING = "cancelling"
	SUCCEEDED = "succeeded"
	FAILED = "failed"
	CANCELLED = "cancelled"
	INTERRUPTED = "interrupted"

	FINISHED = frozenset({SUCCEEDED, FAILED, CANCELLED, INTERRUPTED})
	"""The end states."""


@dataclass(frozen=True, kw_only=True, slots=True)
class Operation:
	"""An operation's record."""

	id: str
	"""The operation's ID: 32 lower-case hexadecimal digits."""

	kind: str
	"""What the operation does, such as ``build``, ``fleetUp`` or ``machineStart``."""

	target: str
	"""The image reference, fleet or machine the operation works on."""

	state: str
	"""Where the operation is, one of ``OperationState``."""

	created: datetime
	caller: str
	"""The name of the client certificate that started the operation."""

	step: str | None = None
	"""What the operation is doing now, while it runs."""

	started: datetime | None = None
	finished: datetime | None = None
	result: dict[str, Any] | None = None
	"""The result, once the operation has succeeded; ``OperationHandle.result`` reads it as its typed record."""

	error: ApiError | None = None
	"""Why the operation failed, was cancelled or was interrupted."""

	@property
	def is_finished(self) -> bool:
		"""Whether the operation has reached an end state. A state this SDK does not know is not an end state."""
		return self.state in OperationState.FINISHED


class OperationsClient:
	"""The operation routes: list, inspect, logs, cancel, and waiting for an operation's end."""

	def __init__(self, connection: Connection) -> None:
		self._connection = connection

	def list(self, filters: Mapping[str, str | Sequence[str]] | None = None) -> list[Operation]:
		"""Lists every kept operation, newest first.

		Args:
			filters: Docker-style filters, ``state`` and ``kind``, such as ``{"state": "running"}``.
		"""
		return _wire.read(self._connection.read(_OPERATIONS + str(Query().filters(filters))), list[Operation])

	def inspect(self, id: str) -> Operation:
		"""Reads one operation, named by its ID or a unique prefix of it."""
		return _wire.read(self._connection.read(_path(id)), Operation)

	def logs(self, id: str, *, follow: bool = False, tail: int | None = None) -> Iterator[str]:
		"""Reads the lines an operation's command prints, as the CLI prints them while it follows the operation.

		Args:
			follow: Keep reading new lines until the operation finishes.
			tail: Start with only this many of the last lines; None reads every line.
		"""
		query = Query().explicit("follow", follow).add("tail", tail)
		return self._connection.lines(_path(id) + "/log" + str(query))

	def cancel(self, id: str) -> Operation:
		"""Asks an operation to stop; it moves to ``cancelling`` and then ``cancelled``. A finished operation is returned unchanged."""
		return _wire.read(self._connection.change("POST", _path(id) + "/cancel").json(), Operation)

	def wait(self, id: str, *, timeout: float | None = None) -> Operation:
		"""Waits until an operation finishes, whether it succeeded or not, and gives its last record.

		Args:
			timeout: The longest wait in seconds; None waits without limit.

		Raises:
			TimeoutError: The operation had not finished when the time-out passed. It carries on.
		"""
		deadline = None if timeout is None else time.monotonic() + timeout
		while True:
			operation = self.inspect(id)
			if operation.is_finished:
				return operation
			if deadline is not None and time.monotonic() >= deadline:
				raise TimeoutError(f"The operation {id} had not finished after {timeout} seconds; it is {operation.state}.")
			time.sleep(_POLL_SECONDS)


class OperationHandle(Generic[T]):
	"""An operation that a route started, with what the caller does with it: follow its log, wait for it, cancel it, or read its
	result as the record its route gives.
	"""

	def __init__(self, operations: OperationsClient, started: Operation, result_type: type[T]) -> None:
		self._operations = operations
		self._result_type = result_type
		self.started = started
		"""The operation's record as the route answered with it."""

	@property
	def id(self) -> str:
		"""The operation's ID."""
		return self.started.id

	def inspect(self) -> Operation:
		"""Reads the operation's record as it is now."""
		return self._operations.inspect(self.id)

	def logs(self, *, follow: bool = True, tail: int | None = None) -> Iterator[str]:
		"""Reads the operation's log; by default, keeps reading until the operation finishes."""
		return self._operations.logs(self.id, follow=follow, tail=tail)

	def cancel(self) -> Operation:
		"""Asks the operation to stop."""
		return self._operations.cancel(self.id)

	def wait(self, *, timeout: float | None = None) -> Operation:
		"""Waits until the operation finishes, whether it succeeded or not, and gives its last record.

		Raises:
			TimeoutError: The operation had not finished when ``timeout`` seconds passed. It carries on.
		"""
		return self._operations.wait(self.id, timeout=timeout)

	def result(self, *, timeout: float | None = None) -> T:
		"""Waits until the operation finishes, and gives its result.

		Raises:
			OperationFailedError: The operation failed, was cancelled or was interrupted; its ``code`` says why, such as
				``BUILD_FAILED``.
			TimeoutError: The operation had not finished when ``timeout`` seconds passed. It carries on.
		"""
		finished = self.wait(timeout=timeout)
		if finished.state != OperationState.SUCCEEDED:
			raise OperationFailedError(finished)
		return _wire.read(finished.result, self._result_type)


def handle_of(operations: OperationsClient, answer: httpx.Response, result_type: type[T]) -> OperationHandle[T]:
	"""Makes the handle of the operation that a route's answer names."""
	return OperationHandle(operations, _wire.read(answer.json(), Operation), result_type)


def _path(id: str) -> str:
	return f"{_OPERATIONS}/{segment(id)}"
