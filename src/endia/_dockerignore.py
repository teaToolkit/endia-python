"""The ``.dockerignore`` rules by which a build leaves files of its context folder out of the archive it sends.

The rules are those the ``endia`` CLI applies, which are Docker's: each line is a pattern anchored at the context's root; a
leading ``/`` says nothing more; ``*`` and ``?`` match within one name and never match ``/``; ``**`` matches any number of whole
folders, or anything at the end of a pattern; ``[...]`` is a character class; a backslash makes the next character literal; a
line starting with ``!`` takes back what earlier lines excluded; the last line that matches decides; and a path is left out when
any folder above it is.

Matching steps through the path one character at a time, keeping every place in the pattern the text so far could have reached,
so a pattern of many ``**`` takes time in proportion to the path's length and never stalls a build.
"""

from collections.abc import Callable
from dataclasses import dataclass

IGNORE_FILE_NAME = ".dockerignore"
# EastIndia's name for an ignore file, which a build reads in preference to a ``.dockerignore`` in the same place.
ENDIA_IGNORE_FILE_NAME = ".endiaignore"


class IgnoreFileError(ValueError):
	"""An ignore file holds a pattern that is not valid. The message names the file, the line and the column of each fault."""


@dataclass(frozen=True, slots=True)
class _Literal:
	character: str


@dataclass(frozen=True, slots=True)
class _OneCharacter:
	"""``?``: any one character but ``/``."""


@dataclass(frozen=True, slots=True)
class _Characters:
	"""``*``: any run of characters, none of them ``/``."""


@dataclass(frozen=True, slots=True)
class _Folders:
	"""``**`` followed by more of the pattern: any number of whole folders, each ending in ``/``, including none."""


@dataclass(frozen=True, slots=True)
class _Rest:
	"""``**`` at the end of the pattern: anything at all."""


@dataclass(frozen=True, slots=True)
class _Class:
	"""``[...]``: one character among ranges, or with ``^`` one character outside them."""

	ranges: tuple[tuple[str, str], ...]
	negated: bool

	def accepts(self, character: str) -> bool:
		inside = any(low <= character <= high for low, high in self.ranges)
		return inside != self.negated


_Token = _Literal | _OneCharacter | _Characters | _Folders | _Rest | _Class

# A place in a pattern is a pair. The first member is the index of the pattern's next piece to match. The second says whether
# the text read so far has started the folders of a '**' at that index; once started, the match leaves those folders only at a
# '/'.
_Place = tuple[int, bool]


@dataclass(frozen=True, slots=True)
class IgnorePattern:
	"""One line of an ignore file."""

	tokens: tuple[_Token, ...]
	reincludes: bool
	"""Whether the line started with ``!`` and so takes back what earlier lines excluded."""

	def matches(self, path: str) -> bool:
		"""Tells whether the pattern matches the whole of a cleaned context path."""
		places = self._closure({(0, False)})
		for character in path:
			places = self._closure({after for place in places for after in self._step(place, character)})
			if not places:
				return False
		return (len(self.tokens), False) in places

	def _step(self, place: _Place, character: str) -> list[_Place]:
		index = place[0]
		if index == len(self.tokens):
			return []
		token = self.tokens[index]
		if isinstance(token, _Folders):
			return [(index, True), (index + 1, False)] if character == "/" else [(index, True)]
		if isinstance(token, (_Characters, _Rest)):
			return [place] if isinstance(token, _Rest) or character != "/" else []
		if isinstance(token, _Literal):
			accepted = character == token.character
		elif isinstance(token, _OneCharacter):
			accepted = character != "/"
		else:
			accepted = token.accepts(character)
		return [(index + 1, False)] if accepted else []

	def _closure(self, places: set[_Place]) -> set[_Place]:
		# A token that can match no text at all lets the pattern move past it without reading a character: a '*', a '**' at the
		# end, and a '**' whose folders have not started.
		pending = list(places)
		reached = set(places)
		while pending:
			index, inside = pending.pop()
			if index < len(self.tokens) and not inside and isinstance(self.tokens[index], (_Characters, _Folders, _Rest)):
				after = (index + 1, False)
				if after not in reached:
					reached.add(after)
					pending.append(after)
		return reached


def compile_pattern(pattern: str, reincludes: bool) -> IgnorePattern:
	"""Reads a cleaned pattern, without its leading ``/`` or ``!``.

	Raises:
		ValueError: A ``[`` has no closing ``]``, or a backslash ends the pattern.
	"""
	tokens: list[_Token] = []
	index = 0
	while index < len(pattern):
		character = pattern[index]
		if character == "*":
			index = _read_stars(pattern, index, tokens)
			continue
		if character == "[":
			index = _read_class(pattern, index, tokens)
			continue
		if character == "?":
			tokens.append(_OneCharacter())
		elif character == "\\":
			if index + 1 >= len(pattern):
				raise ValueError("a trailing '\\' escapes nothing")
			index += 1
			tokens.append(_Literal(pattern[index]))
		else:
			tokens.append(_Literal(character))
		index += 1
	return IgnorePattern(tuple(tokens), reincludes)


def _read_stars(pattern: str, index: int, tokens: list[_Token]) -> int:
	# A single '*' stays within one name. A '**' also matches the '/' after it, so that 'src/**/obj' matches 'src/obj'.
	if index + 1 >= len(pattern) or pattern[index + 1] != "*":
		tokens.append(_Characters())
		return index + 1
	following = index + 2
	if following < len(pattern) and pattern[following] == "/":
		following += 1
	tokens.append(_Rest() if following >= len(pattern) else _Folders())
	return following


def _read_class(pattern: str, index: int, tokens: list[_Token]) -> int:
	# In a class, '^' first negates, a backslash makes the next character literal, 'a-z' is a range, and a ']' first in the class
	# is a literal.
	position = index + 1
	negated = position < len(pattern) and pattern[position] == "^"
	if negated:
		position += 1
	ranges: list[tuple[str, str]] = []
	members = 0
	range_open = False
	while position < len(pattern) and (pattern[position] != "]" or members == 0):
		character = pattern[position]
		if character == "\\":
			position += 1
			if position >= len(pattern):
				break
			character = pattern[position]
		elif character == "-" and members > 0 and position + 1 < len(pattern) and pattern[position + 1] != "]":
			range_open = True
			members += 1
			position += 1
			continue
		if range_open:
			ranges[-1] = (ranges[-1][0], character)
			range_open = False
		else:
			ranges.append((character, character))
		members += 1
		position += 1
	if position >= len(pattern):
		raise ValueError("a '[' has no closing ']'")
	tokens.append(_Class(tuple(ranges), negated))
	return position + 1


def clean(path: str) -> str:
	"""Cleans a ``/``-separated path by its text alone: empty and ``.`` names are dropped, and ``..`` removes the name before it.

	A path starting with ``/`` never climbs above its root; a relative one keeps its leading ``..`` names. An empty result is
	``.`` for a relative path and ``/`` for a rooted one.
	"""
	rooted = path.startswith("/")
	names: list[str] = []
	for name in path.split("/"):
		if name in ("", "."):
			continue
		if name == "..":
			if names and names[-1] != "..":
				names.pop()
			elif not rooted:
				names.append("..")
			continue
		names.append(name)
	joined = "/".join(names)
	if rooted:
		return "/" + joined
	return joined or "."


class DockerIgnore:
	"""The patterns of one ignore file, which decide whether a path of the context is left out."""

	def __init__(self, patterns: tuple[IgnorePattern, ...] = ()) -> None:
		self._patterns = patterns

	@classmethod
	def parse(cls, text: str, file_name: str = IGNORE_FILE_NAME) -> "DockerIgnore":
		"""Reads an ignore file's text.

		Lines end in LF or CRLF, and a leading byte-order mark is dropped. Blank lines and lines whose first non-blank character is
		``#`` are skipped.

		Raises:
			IgnoreFileError: A line holds a pattern that is not valid, or a ``!`` with no pattern after it.
		"""
		patterns: list[IgnorePattern] = []
		faults: list[str] = []
		for number, raw in enumerate(text.removeprefix("﻿").split("\n"), start=1):
			line = raw.rstrip("\r")
			trimmed = line.strip()
			if not trimmed or trimmed.startswith("#"):
				continue
			column = len(line) - len(line.lstrip()) + 1
			reincludes = trimmed.startswith("!")
			body = trimmed[1:].strip() if reincludes else trimmed
			if not body:
				faults.append(f"{file_name}:{number}:{column}: A '!' needs a pattern after it.")
				continue
			cleaned = clean(body)
			if len(cleaned) > 1 and cleaned.startswith("/"):
				cleaned = cleaned[1:]
			try:
				patterns.append(compile_pattern(cleaned, reincludes))
			except ValueError as problem:
				faults.append(f"{file_name}:{number}:{column}: The pattern '{trimmed}' is not valid: {problem}.")
		if faults:
			raise IgnoreFileError("\n".join(faults))
		return cls(tuple(patterns))

	def is_ignored(self, path: str) -> bool:
		"""Tells whether a context path, relative to the context's root and written with ``/``, is left out.

		The patterns apply in file order, and the last one that matches the path or any folder above it decides. The context's
		root itself is never left out.

		Raises:
			ValueError: The path points outside the context.
		"""
		cleaned = clean(path).lstrip("/")
		if cleaned == ".." or cleaned.startswith("../"):
			raise ValueError(f"The path '{path}' points outside the build context.")
		if cleaned in ("", "."):
			return False
		names = cleaned.split("/")
		folders = ["/".join(names[:count]) for count in range(1, len(names))]
		matches: Callable[[IgnorePattern], bool] = lambda pattern: pattern.matches(cleaned) or any(map(pattern.matches, folders))
		ignored = False
		for pattern in self._patterns:
			# A line that takes back can only undo a match, and an excluding line can only add one, so a line that could change
			# nothing is skipped.
			if pattern.reincludes == ignored and matches(pattern):
				ignored = not pattern.reincludes
		return ignored
