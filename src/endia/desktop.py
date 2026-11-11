"""The desktop of a running machine: programs started in the signed-in user's session, and their windows read and driven
through UI Automation.

An element is named by a selector, a path of segments separated by ``/``. A segment names at least one of a control type, an
exact name in brackets, and an automation ID after ``#``, and may end in ``:<n>`` to pick the n-th of several matches, counting
from 1; a backslash makes the next ``]``, ``/``, ``#``, ``:`` or backslash literal. The first segment always names one of the
process's visible top-level windows, and each further segment matches among the descendants of the previous match. For example,
``Window[Sign in]/Edit#UserName`` is the edit box with automation ID ``UserName`` in the window titled ``Sign in``, and
``Window/Button[Save]`` is the Save button in the process's one window, whatever its title.

A selector that matches nothing is refused with ``ELEMENT_NOT_FOUND`` and one that matches several with ``ELEMENT_AMBIGUOUS``;
both carry the window's UI tree in ``error.details["tree"]``, so that the caller can correct the selector.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from . import _wire
from ._connection import Connection
from ._query import segment
from .processes import ProcessWindow, WindowBounds


@dataclass(frozen=True, kw_only=True, slots=True)
class DesktopLaunchResult:
	"""A program started in the desktop user's session."""

	process_id: int
	started: datetime
	"""When the process started, which the other desktop and process routes take as ``started``."""

	session_id: int
	user: str
	integrity_level: str
	windows: list[ProcessWindow]
	"""The windows the process had shown when it was found to exist; usually none yet."""


@dataclass(frozen=True, kw_only=True, slots=True)
class DesktopScreenshot:
	"""A PNG of the desktop, or of a process's largest visible window."""

	png: bytes
	width: int
	height: int
	session_id: int
	taken: datetime


@dataclass(frozen=True, kw_only=True, slots=True)
class UiElement:
	"""One element of a window's UI tree, as UI Automation's control view shows it."""

	control_type: str
	"""UI Automation's control type without ``ControlType.``, such as ``Button``."""

	enabled: bool
	visible: bool
	is_password: bool
	patterns: list[str]
	"""The UI Automation patterns the element supports, such as ``Invoke`` or ``Value``."""

	children: list[UiElement]
	name: str | None = None
	automation_id: str | None = None
	class_name: str | None = None
	value: str | None = None
	"""The element's value; None when it has none or is a password field, whose value is never read."""

	bounds: WindowBounds | None = None
	"""Where the element is, from the top-left corner of its window."""

	screen_bounds: WindowBounds | None = None
	"""Where the element is, from the top-left corner of the primary screen."""


@dataclass(frozen=True, kw_only=True, slots=True)
class InspectedWindow:
	"""One window of a process, with its UI tree."""

	handle: str
	title: str
	root: UiElement


@dataclass(frozen=True, kw_only=True, slots=True)
class DesktopWaitResult:
	"""What a wait found: the element and its window, or neither when the wait was for the element to go."""

	window: str | None = None
	element: UiElement | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class PointerResult:
	"""Where the pointer acted: the screen point, and the top-level window there with the process that owns it."""

	screen_x: int
	screen_y: int
	window: str | None = None
	process_id: int | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class DesktopWindow:
	"""One visible top-level window on the desktop."""

	process_id: int
	process_name: str
	handle: str
	title: str
	class_name: str
	bounds: WindowBounds


@dataclass(frozen=True, kw_only=True, slots=True)
class TrayIcon:
	"""One icon of the notification area."""

	name: str
	"""The name Windows shows for the icon, usually the program's tooltip."""

	hidden: bool
	"""Whether Windows keeps the icon behind the "Show hidden icons" button."""

	screen_bounds: WindowBounds | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class TrayClickResult:
	"""The icon that was clicked, and where."""

	name: str
	screen_x: int
	screen_y: int


@dataclass(frozen=True, kw_only=True, slots=True)
class UacPromptDetail:
	"""What an elevation prompt showed."""

	program: str
	accounts: list[str]
	publisher: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class UacResult:
	"""How an elevation prompt was answered."""

	outcome: str
	"""``accepted`` when the prompt closed after Yes, ``credentialsRefused`` when it still shows after Yes and is left open, or
	``cancelled`` after No."""

	prompt: UacPromptDetail
	screenshot_before: str
	"""A PNG of the desktop before the answer, Base64 encoded."""

	screenshot_after: str
	"""A PNG of the desktop after the answer, Base64 encoded."""


class DesktopClient:
	"""The desktop routes of a running machine, which act in the desktop user's session.

	A route that acts on a window names its process by the process ID and, optionally, the time the process started.
	"""

	def __init__(self, connection: Connection) -> None:
		self._connection = connection

	def launch(
		self,
		machine: str,
		program: str,
		args: list[str] | tuple[str, ...] = (),
		*,
		working_dir: str | None = None,
		env: list[str] | tuple[str, ...] = (),
		elevated: bool = False,
	) -> DesktopLaunchResult:
		"""Starts a program in the desktop user's session, and answers once the process exists, without waiting for a window.

		Args:
			env: Extra variables, each ``NAME=value``.
			elevated: Use the desktop user's administrator token; a standard user is refused with ``CAPABILITY_MISSING``.
		"""
		body = {"program": program, "args": list(args), "workingDir": working_dir, "env": list(env), "elevated": elevated}
		return _wire.read(self._post(machine, "launch", body), DesktopLaunchResult)

	def screenshot(self, machine: str, *, process_id: int | None = None) -> DesktopScreenshot:
		"""Takes a PNG of the whole desktop, or of a process's largest visible window."""
		# A screenshot changes nothing in the machine. Its request therefore carries no Idempotency-Key, and the service keeps no
		# copy of the image.
		answer = self._connection.unkeyed(_desktop(machine, "screenshot"), {"processId": process_id})
		return DesktopScreenshot(
			png=answer.content,
			width=int(answer.headers["X-EastIndia-Width"]),
			height=int(answer.headers["X-EastIndia-Height"]),
			session_id=int(answer.headers["X-EastIndia-Session"]),
			taken=_wire.Timestamp.parse(answer.headers["X-EastIndia-Taken"]),
		)

	def inspect(
		self, machine: str, process_id: int, *, started: datetime | None = None, window: str | None = None
	) -> list[InspectedWindow]:
		"""Reads the UI tree of a process's windows, or of the one window whose handle is given."""
		body = {"processId": process_id, "started": _time(started), "window": window}
		return _wire.read(self._post(machine, "inspect", body)["windows"], list[InspectedWindow])

	def wait(
		self,
		machine: str,
		process_id: int,
		element: str,
		*,
		gone: bool = False,
		timeout: int | None = None,
		started: datetime | None = None,
	) -> DesktopWaitResult:
		"""Waits until a selector names exactly one element, or with ``gone`` none.

		The service reads the process's windows every half second. It answers only when the wait ends, so no time limit of the
		client's own applies.

		Args:
			timeout: The longest wait in seconds, 1 to 3600; None takes the host's ``desktopActionTimeoutSeconds``. When it passes,
				the wait is refused with ``TIMEOUT``, with the last tree read in ``error.details["tree"]``.
		"""
		body = {"processId": process_id, "started": _time(started), "element": element, "gone": gone, "timeoutSeconds": timeout}
		answer = self._connection.change("POST", _desktop(machine, "wait"), json_body=_present(body), timed=False)
		return _wire.read(answer.json(), DesktopWaitResult)

	def invoke(self, machine: str, process_id: int, element: str, *, started: datetime | None = None) -> None:
		"""Invokes an element through its Invoke pattern, as a button is pressed."""
		self._post(machine, "invoke", {"processId": process_id, "started": _time(started), "element": element})

	def click(
		self,
		machine: str,
		process_id: int,
		element: str,
		*,
		button: str = "left",
		double: bool = False,
		started: datetime | None = None,
	) -> PointerResult:
		"""Clicks the centre of an element with the pointer, for an element that answers only the mouse, such as a link.

		Args:
			button: ``left``, ``right`` or ``middle``.
			double: Double-click.
		"""
		body = {"processId": process_id, "started": _time(started), "element": element, "button": button, "doubleClick": double}
		return _wire.read(self._post(machine, "click", body), PointerResult)

	def set_value(self, machine: str, process_id: int, element: str, value: str, *, started: datetime | None = None) -> str | None:
		"""Sets an element's value through its Value pattern, and gives the value read back. A password field is refused with
		``ACTION_UNSUPPORTED``; use ``set_secret`` for it.
		"""
		body = {"processId": process_id, "started": _time(started), "element": element, "value": value, "secret": False}
		read_back: str | None = self._post(machine, "set-value", body).get("value")
		return read_back

	def set_secret(self, machine: str, process_id: int, element: str, secret: str, *, started: datetime | None = None) -> None:
		"""Sets an element's value to a secret, such as a password, which may go into a password field.

		The service never logs, records or prints the secret, and does not read it back. The request is sent once, with no
		``Idempotency-Key``, no repeat after a failed connection and no time limit of the client's own, so that no stored reply
		holds it.
		"""
		body = {"processId": process_id, "started": _time(started), "element": element, "value": secret, "secret": True}
		self._connection.once(_desktop(machine, "set-value"), _present(body))

	def keys(self, machine: str, process_id: int, keys: str, *, started: datetime | None = None) -> int:
		"""Brings the process's main window to the front and presses keys, such as ``ctrl+s`` or ``alt+f4 enter``.

		Returns:
			The number of input events sent.
		"""
		sent: int = self._post(machine, "keys", {"processId": process_id, "started": _time(started), "keys": keys})["sent"]
		return sent

	def type_text(self, machine: str, process_id: int, text: str, *, started: datetime | None = None) -> int:
		"""Brings the process's main window to the front and types text literally, as Unicode characters.

		Returns:
			The number of input events sent.
		"""
		sent: int = self._post(machine, "keys", {"processId": process_id, "started": _time(started), "text": text})["sent"]
		return sent

	def pointer(
		self,
		machine: str,
		action: str,
		x: int,
		y: int,
		*,
		process_id: int | None = None,
		started: datetime | None = None,
		button: str = "left",
		delta: int = 1,
	) -> PointerResult:
		"""Moves, clicks or scrolls the pointer.

		Args:
			action: ``move``, ``click``, ``double-click`` or ``scroll``.
			x: Physical pixels from the left of the process's main window's client area, or of the primary screen without a process.
			y: Physical pixels from the top, measured the same way.
			process_id: The process whose main window is brought to the front first; None acts on the screen.
			delta: The notches a scroll turns the wheel; negative scrolls down.
		"""
		body = {"processId": process_id, "started": _time(started), "action": action, "x": x, "y": y, "button": button, "delta": delta}
		return _wire.read(self._post(machine, "pointer", body), PointerResult)

	def windows(self, machine: str) -> list[DesktopWindow]:
		"""Lists every visible top-level window on the desktop, frontmost first, with the process that owns each."""
		return _wire.read(self._connection.read(_desktop(machine, "windows")), list[DesktopWindow])

	def tray(self, machine: str) -> list[TrayIcon]:
		"""Lists the icons of the notification area, in the order shown."""
		return _wire.read(self._connection.read(_desktop(machine, "tray")), list[TrayIcon])

	def tray_click(self, machine: str, icon: str, *, button: str = "left", double: bool = False) -> TrayClickResult:
		"""Clicks an icon of the notification area, named by the start of its name.

		An icon whose whole name is ``icon`` is chosen even when other names start with it; otherwise exactly one icon's name must
		start with it. A menu the program opens is one of that program's windows, which the other routes reach by its process ID.
		"""
		return _wire.read(self._post(machine, "tray/click", {"icon": icon, "button": button, "doubleClick": double}), TrayClickResult)

	def answer_prompt(
		self,
		machine: str,
		expected_program: str,
		action: str,
		*,
		user: str | None = None,
		password: str | None = None,
		timeout: int | None = None,
	) -> UacResult:
		"""Waits for an elevation prompt in the desktop user's session and answers it: ``endia desktop uac``.

		A prompt that names another program is refused with ``UNEXPECTED_PROMPT`` and left untouched. The request carries a password,
		so it is sent once, with no ``Idempotency-Key``, no repeat after a failed connection and no time limit of the client's own.

		Args:
			expected_program: The file name the prompt must name, such as ``diagnostics.exe``, compared without regard to case.
			action: ``credentials`` to choose ``user`` and type ``password``, ``consent`` to press Yes, or ``cancel`` to press No.
			timeout: The seconds to wait for the prompt; None takes the host's ``desktopActionTimeoutSeconds``.
		"""
		body = {"expectedProgram": expected_program, "action": action, "user": user, "password": password, "timeoutSeconds": timeout}
		return _wire.read(self._connection.once(_desktop(machine, "uac"), _present(body)).json(), UacResult)

	def _post(self, machine: str, action: str, body: dict[str, Any]) -> Any:
		return self._connection.change("POST", _desktop(machine, action), json_body=_present(body)).json()


def _desktop(machine: str, action: str) -> str:
	return f"/v1/machines/{segment(machine)}/desktop/{action}"


def _time(value: datetime | None) -> str | None:
	return _wire.format_time(value) if value is not None else None


def _present(body: dict[str, Any]) -> dict[str, Any]:
	# The service gives a member that is left out its default value. It refuses null in a member that may not be null, so a
	# member whose value is None is left out.
	return {name: value for name, value in body.items() if value is not None}
