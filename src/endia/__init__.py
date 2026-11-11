"""The Python client of EastIndia, which runs Hyper-V Windows virtual machines for tests, driven as Docker drives containers.

Start from ``EastIndiaClient``, or ``EastIndiaClient.from_env()`` to connect where the ``endia`` CLI would. The records of
each area live in the area's module, such as ``endia.desktop.UiElement``.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

from ._dockerignore import IgnoreFileError
from ._wire import Timestamp
from .client import EastIndiaClient
from .errors import (
	ApiError,
	ConnectionFailure,
	EastIndiaApiError,
	EastIndiaConnectionError,
	EastIndiaError,
	ErrorCode,
	ExecTimeoutError,
	MachineFailedError,
	OperationFailedError,
)
from .execs import ExecResult, ExecSession, StreamKind
from .operations import Operation, OperationHandle, OperationState

try:
	__version__ = _version("endia")
except PackageNotFoundError:
	__version__ = "0.0.0"

__all__ = [
	"ApiError",
	"ConnectionFailure",
	"EastIndiaApiError",
	"EastIndiaClient",
	"EastIndiaConnectionError",
	"EastIndiaError",
	"ErrorCode",
	"ExecResult",
	"ExecSession",
	"ExecTimeoutError",
	"IgnoreFileError",
	"MachineFailedError",
	"Operation",
	"OperationFailedError",
	"OperationHandle",
	"OperationState",
	"StreamKind",
	"Timestamp",
	"__version__",
]
