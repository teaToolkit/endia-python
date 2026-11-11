"""A route's path and query string as the API reads them."""

import json
from collections.abc import Iterable, Mapping, Sequence
from urllib.parse import quote


def segment(value: str) -> str:
	"""Escapes one path segment, such as a machine name, so that a ``/`` in it stays inside the segment."""
	return quote(value, safe="")


def reference(value: str) -> str:
	"""Escapes an image reference, which keeps its ``/`` separators because the image routes take the rest of the path."""
	return "/".join(segment(part) for part in value.split("/"))


class Query:
	"""Builds a query string. A parameter without a value is left out, so that the service applies its default.

	A boolean is written as ``true`` and left out when false, unless it is added with ``explicit``. A list repeats its parameter.
	"""

	def __init__(self) -> None:
		self._pairs: list[tuple[str, str]] = []

	def add(self, name: str, value: str | int | float | None) -> "Query":
		"""Adds a parameter whose value is text or a number; nothing is added for None."""
		if value is not None:
			self._pairs.append((name, str(value)))
		return self

	def flag(self, name: str, value: bool) -> "Query":
		"""Adds a boolean that the route reads as false when it is missing: ``true`` is written, and false is left out."""
		return self.add(name, "true") if value else self

	def explicit(self, name: str, value: bool) -> "Query":
		"""Adds a boolean written out, false included, for a route that reads a missing parameter as true."""
		return self.add(name, "true" if value else "false")

	def each(self, name: str, values: Iterable[str]) -> "Query":
		"""Adds a list: the parameter once for each value."""
		for value in values:
			self.add(name, value)
		return self

	def json(self, name: str, value: Mapping[str, object] | None) -> "Query":
		"""Adds a parameter whose value is a JSON object, such as ``filters`` or ``buildargs``; nothing is added for none or empty."""
		return self.add(name, json.dumps(value, separators=(",", ":"))) if value else self

	def filters(self, filters: Mapping[str, str | Sequence[str]] | None) -> "Query":
		"""Adds the ``filters`` parameter of a listing route, each filter's values as a list, as Docker's filters are written."""
		if not filters:
			return self
		return self.json("filters", {name: [values] if isinstance(values, str) else list(values) for name, values in filters.items()})

	def __str__(self) -> str:
		if not self._pairs:
			return ""
		return "?" + "&".join(f"{name}={quote(value, safe='')}" for name, value in self._pairs)
