"""The ignore-file rules. The expected values are those of the C# tests of Shared.EastIndiaFile's DockerIgnore, which the CLI
applies, so that the SDK leaves out exactly the files the CLI leaves out.
"""

import time

import pytest

from endia import IgnoreFileError
from endia._dockerignore import DockerIgnore

LEDGER_DESKTOP = """\
**/bin/Debug
**/obj
*.user
.git
logs
!logs/sample.log
"""

# Examples of use


@pytest.mark.parametrize(
	("path", "ignored"),
	[
		("publish/Ledger.exe", False),
		("src/Ledger/bin/Debug/net10.0/Ledger.dll", True),
		("src/Ledger/bin/Release/net10.0/Ledger.dll", False),
		("src/Ledger/obj/project.assets.json", True),
		("Ledger.csproj.user", True),
		("src/Ledger/Ledger.csproj.user", False),
		(".git/config", True),
		("logs/2026-10-05.log", True),
		("logs/sample.log", False),
	],
)
def test_ledger_desktop_context_leaves_out_build_output_and_logs_but_the_sample(path: str, ignored: bool) -> None:
	assert DockerIgnore.parse(LEDGER_DESKTOP).is_ignored(path) is ignored


@pytest.mark.parametrize(
	("pattern", "path", "ignored"),
	[
		("*.log", "setup.log", True),
		("*.log", "logs/setup.log", False),
		("**/*.log", "setup.log", True),
		("**/*.log", "logs/2026/setup.log", True),
		("logs/**", "logs/2026/setup.log", True),
		("logs/**", "logs", False),
		("src/**/obj", "src/obj", True),
		("src/**/obj", "src/Ledger/Core/obj", True),
		("a*b", "a/b", False),
		("a**b", "a/b", True),
		("setup.?og", "setup.log", True),
		("setup.?og", "setup/log", False),
		("[abc].txt", "b.txt", True),
		("[^abc].txt", "b.txt", False),
		("[a-c]x", "bx", True),
		("[a\\-c]x", "-x", True),
		("[]]x", "]x", True),
		("draft\\*", "draft*", True),
		("draft\\*", "drafts", False),
		("v1.0(rc)+", "v1.0(rc)+", True),
		("v1.0", "v1x0", False),
		("/build", "build", True),
		("/build", "src/build", False),
		("./build/", "build", True),
		("Readme.md", "readme.md", False),
	],
)
def test_pattern_matches_as_dockers_matcher_does(pattern: str, path: str, ignored: bool) -> None:
	assert DockerIgnore.parse(pattern).is_ignored(path) is ignored


def test_comments_blank_lines_crlf_and_byte_order_mark_are_skipped() -> None:
	ignore = DockerIgnore.parse("﻿# editor files\r\n\r\n  *.user\r\n!keep.user\r\n")

	# Had the line "# editor files" been read as a pattern, it would leave out a file named "# editor files".
	assert not ignore.is_ignored("# editor files")
	assert ignore.is_ignored("Ledger.csproj.user")
	assert not ignore.is_ignored("keep.user")


def test_line_that_takes_back_before_the_excluding_line_is_overridden_by_it() -> None:
	assert DockerIgnore.parse("!logs/sample.log\nlogs\n").is_ignored("logs/sample.log")


def test_paths_are_cleaned_and_the_context_root_is_never_left_out() -> None:
	ignore = DockerIgnore.parse("**")

	assert ignore.is_ignored("./publish/../publish//Ledger.exe")
	assert not ignore.is_ignored(".")


def test_many_double_stars_against_a_deep_path_finish_at_once() -> None:
	ignore = DockerIgnore.parse("**/**/**/**/**/x")
	deep = "/".join(["dir"] * 200)

	started = time.monotonic()
	assert not ignore.is_ignored(deep + "/y")
	assert ignore.is_ignored(deep + "/x")
	assert time.monotonic() - started < 5


# Examples of misuse


def test_malformed_patterns_are_each_reported_with_line_and_column() -> None:
	with pytest.raises(IgnoreFileError) as refused:
		DockerIgnore.parse("**/obj\n  [abc\n!\nlogs\\\n")

	assert str(refused.value).splitlines() == [
		".dockerignore:2:3: The pattern '[abc' is not valid: a '[' has no closing ']'.",
		".dockerignore:3:1: A '!' needs a pattern after it.",
		".dockerignore:4:1: The pattern 'logs\\' is not valid: a trailing '\\' escapes nothing.",
	]


def test_path_outside_the_context_is_refused() -> None:
	with pytest.raises(ValueError, match="outside the build context"):
		DockerIgnore().is_ignored("../secrets/key.pfx")
