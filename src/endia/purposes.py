"""Purposes: what a fleet or a machine is being used for, as its caller said when setting it.

A purpose is a one-line title with optional Markdown notes, its detail. EastIndia stores and shows a purpose and does nothing else
with it. A machine without a purpose of its own has its fleet's. The routes that read, set and clear a purpose are
``FleetsClient.purpose`` and ``MachinesClient.purpose`` with their ``set_purpose`` and ``clear_purpose``, which
``endia purpose show``, ``set`` and ``clear`` use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

MAX_DETAIL_BYTES = 65536
"""The most bytes a purpose's detail may take in UTF-8: 64 KiB."""

# Carriage return, line feed, next line, line separator and paragraph separator: each ends a line in some reader.
_LINE_BREAKS = "\r\n\u0085\u2028\u2029"


class PurposeSource:
	"""Where the purpose in effect for a machine comes from."""

	MACHINE = "machine"
	"""The machine's own purpose."""

	FLEET = "fleet"
	"""The purpose of the machine's fleet, because the machine has none of its own."""


class PurposeAction:
	"""The words of a history entry's ``action``."""

	SET = "set"
	"""A caller stored a purpose, whether or not one was stored before."""

	CLEARED = "cleared"
	"""A caller removed the purpose that was stored."""


@dataclass(frozen=True, kw_only=True, slots=True)
class PurposeSummary:
	"""A purpose without its detail, as the lists of fleets and machines carry it."""

	title: str
	set_by: str
	"""The caller who stored the purpose: the client certificate's name over HTTPS, or ``npipe:`` and the Windows account."""

	set: datetime


@dataclass(frozen=True, kw_only=True, slots=True)
class PurposeDetail:
	"""A whole purpose."""

	title: str
	"""What the fleet or machine is being used for, in one line."""

	set_by: str
	set: datetime
	detail: str | None = None
	"""The caller's notes in Markdown, exactly as received. A sandbox agent may have written them, so a program that renders them
	should fetch no remote image, open a link only when a person clicks it, and show raw HTML as text."""


@dataclass(frozen=True, kw_only=True, slots=True)
class PurposeHistoryEntry:
	"""One change to the purpose of a fleet or machine."""

	time: datetime
	action: str
	"""One of ``PurposeAction``."""

	set_by: str
	title: str | None = None
	"""The title that was set, or None for a clearing."""

	detail: str | None = None
	"""The detail that was set, or None for a clearing or a purpose set without detail."""


@dataclass(frozen=True, kw_only=True, slots=True)
class FleetPurpose:
	"""A fleet's purpose with every change to it while the fleet has existed: ``endia purpose show``."""

	history: list[PurposeHistoryEntry]
	"""The changes, oldest first."""

	purpose: PurposeDetail | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class FleetPurposeResult:
	"""A fleet's purpose once a change is stored."""

	purpose: PurposeDetail | None = None
	"""The purpose just set, or None after a clearing."""


@dataclass(frozen=True, kw_only=True, slots=True)
class MachinePurpose:
	"""A machine's own purpose with every change to it, and the purpose in effect for the machine: ``endia purpose show``."""

	history: list[PurposeHistoryEntry]
	"""The changes to the machine's own purpose, oldest first. Changes to its fleet's purpose are in the fleet's history."""

	purpose: PurposeDetail | None = None
	"""The machine's own purpose."""

	effective: PurposeDetail | None = None
	"""The machine's own purpose, else its fleet's, else None."""

	purpose_source: str | None = None
	"""One of ``PurposeSource``, or None when no purpose is in effect."""


@dataclass(frozen=True, kw_only=True, slots=True)
class MachinePurposeResult:
	"""A machine's own purpose once a change is stored, and the purpose in effect then."""

	purpose: PurposeDetail | None = None
	"""The machine's own purpose just set, or None after a clearing."""

	effective: PurposeDetail | None = None
	"""After a clearing, the machine's fleet's purpose, or None."""

	purpose_source: str | None = None
	"""One of ``PurposeSource``, or None when no purpose is in effect."""


def purpose_body(title: str, detail: str | None) -> dict[str, Any]:
	"""Gives the JSON of a purpose a caller sets: the body of ``PUT .../purpose`` and the content of ``purpose.json`` in the
	archive of ``compose up``. An absent detail is left out of it.

	Raises:
		ValueError: The title has a line break, or the detail takes more than 65536 bytes in UTF-8. The service checks the other
			limits: a title of 1 to 200 characters.
	"""
	if any(mark in title for mark in _LINE_BREAKS):
		raise ValueError("A purpose's title is one line; the title given has a line break.")
	if detail is not None and len(detail.encode("utf-8")) > MAX_DETAIL_BYTES:
		raise ValueError(f"A purpose's detail takes at most {MAX_DETAIL_BYTES} bytes in UTF-8.")
	body: dict[str, Any] = {"title": title}
	if detail is not None:
		body["detail"] = detail
	return body
