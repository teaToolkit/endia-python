"""The API's JSON conventions: camelCase member names, times written to the tenth of a microsecond, and records read tolerantly.

Every record of the SDK is a frozen dataclass whose field names are the API's member names in Python's style. This module turns an
answer's JSON into those records and writes the times a request carries. Without it, each route would map its own members.
"""

import dataclasses
import json
import re
from datetime import datetime, timezone
from typing import Any, TypeVar

import cattrs
from cattrs.gen import make_dict_structure_fn, override

T = TypeVar("T")

WIRE_NAME = "wire"
"""The key of a field's metadata that gives its member name when the camelCase form of the field name is not the member name."""

_TIME = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,7}))?(Z|[+-]\d{2}:\d{2})")
# The API writes seven fractional digits, because Windows keeps times to 100 nanoseconds.
_FRACTION_DIGITS = 7

# A Python datetime keeps six fractional digits: whole microseconds.
_MICROSECOND_DIGITS = 6


class Timestamp(datetime):
	"""A time read from the API, which remembers the text the service wrote.

	The service writes times to 100 nanoseconds, one digit finer than a ``datetime`` holds. A process's start time sent back to
	the service, as the ``started`` check of the process and desktop routes needs, must name exactly the instant the service
	wrote, so the SDK sends a ``Timestamp`` as its own text. In every other respect it is an aware ``datetime`` in UTC.
	"""

	__slots__ = ("text",)
	text: str

	@classmethod
	def parse(cls, text: str) -> "Timestamp":
		"""Reads an RFC 3339 time, such as ``2026-10-05T14:03:22.1234567Z``.

		Raises:
			ValueError: The text is not an RFC 3339 time with seconds and a zone.
		"""
		match = _TIME.fullmatch(text)
		if match is None:
			raise ValueError(f"Expected an RFC 3339 time such as 2026-10-05T14:03:22.1234567Z; found '{text}'.")
		year, month, day, hour, minute, second, fraction, zone = match.groups()
		microseconds = int((fraction or "").ljust(_MICROSECOND_DIGITS, "0")[:_MICROSECOND_DIGITS])
		local = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second), microseconds, tzinfo=_zone(zone))
		utc = local.astimezone(timezone.utc)
		value = cls(utc.year, utc.month, utc.day, utc.hour, utc.minute, utc.second, utc.microsecond, tzinfo=timezone.utc)
		value.text = text
		return value

	def __reduce__(self) -> tuple[Any, ...]:
		return (Timestamp.parse, (self.text,))


def _zone(text: str) -> timezone:
	if text == "Z":
		return timezone.utc
	sign = -1 if text[0] == "-" else 1
	hours, minutes = int(text[1:3]), int(text[4:6])
	return timezone(sign * (datetime(1, 1, 1, hours, minutes) - datetime(1, 1, 1)))


def format_time(value: datetime) -> str:
	"""Writes a time as the API reads it: UTC with seven fractional digits. A ``Timestamp`` is written as the service wrote it.

	Raises:
		ValueError: The time has no zone, so the instant it names is unknown.
	"""
	if isinstance(value, Timestamp):
		return value.text
	if value.tzinfo is None:
		raise ValueError(f"The time {value.isoformat()} has no zone; give an aware datetime, such as one in timezone.utc.")
	utc = value.astimezone(timezone.utc)
	return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond:06d}".ljust(_FRACTION_DIGITS, "0") + "Z"


def camel(name: str) -> str:
	"""Gives the camelCase member name of a snake_case field name, such as ``fieldPath`` for ``field_path``."""
	first, *rest = name.split("_")
	return first + "".join(word[:1].upper() + word[1:] for word in rest)


def wire_name(field: "dataclasses.Field[Any]") -> str:
	"""Gives the member name a record's field is read from."""
	named = field.metadata.get(WIRE_NAME)
	return named if isinstance(named, str) else camel(field.name)


def wire(name: str) -> Any:
	"""Declares a field whose member name is not the camelCase form of its own, such as ``hostReserveMiB``."""
	return dataclasses.field(metadata={WIRE_NAME: name})


def _converter() -> cattrs.Converter:
	# Members a record does not have are skipped, so that an answer from a newer service, which may carry more, stays readable.
	converter = cattrs.Converter(forbid_extra_keys=False)
	converter.register_structure_hook(datetime, lambda value, _: Timestamp.parse(value))

	def structure_hook(cls: type[Any]) -> Any:
		renamed: dict[str, Any] = {field.name: override(rename=wire_name(field)) for field in dataclasses.fields(cls)}
		make: Any = make_dict_structure_fn
		return make(cls, converter, **renamed)

	converter.register_structure_hook_factory(dataclasses.is_dataclass, structure_hook)
	return converter


_CONVERTER = _converter()


def read(data: Any, cls: type[T]) -> T:
	"""Reads parsed JSON as a record, or a list of records, of the SDK.

	Raises:
		ValueError: The JSON lacks a member the record requires, or holds a value of the wrong type.
	"""
	try:
		return _CONVERTER.structure(data, cls)
	except (cattrs.BaseValidationError, KeyError, TypeError, ValueError) as error:
		raise ValueError(f"The service's answer is not a {getattr(cls, '__name__', cls)}: {error}") from error


def read_json_line(line: str, cls: type[T]) -> T:
	"""Reads one line of a stream of JSON objects as a record.

	Raises:
		ValueError: The line is not JSON, or not the record.
	"""
	return read(json.loads(line), cls)
