"""Docker's rules for where a copy lands, and the paths of a machine. The expected placements are the table of ``docker cp``."""

import pytest

from endia.archives import MachinePath, PathMode, Placement, place

# Examples of use


@pytest.mark.parametrize(
	("source_is_folder", "contents_only", "destination", "expected"),
	[
		(False, False, None, Placement(into_destination=False, root_name="app.msi")),
		(False, False, PathMode.FILE, Placement(into_destination=False, root_name="app.msi")),
		(False, False, PathMode.DIRECTORY, Placement(into_destination=True, root_name=None)),
		(True, False, None, Placement(into_destination=False, root_name="app.msi")),
		(True, False, PathMode.DIRECTORY, Placement(into_destination=True, root_name=None)),
		(True, True, PathMode.DIRECTORY, Placement(into_destination=True, root_name="")),
	],
	ids=[
		"file to a new name",
		"file over a file",
		"file into a folder",
		"folder to a new name",
		"folder into a folder as a subfolder",
		"folder's contents into a folder",
	],
)
def test_copy_lands_as_docker_cp_places_it(
	source_is_folder: bool, contents_only: bool, destination: str | None, expected: Placement
) -> None:
	placed = place(
		source_is_folder=source_is_folder,
		source_names_folder=contents_only,
		source_contents_only=contents_only,
		destination_kind=destination,
		destination_names_folder=False,
		destination_name="app.msi",
	)

	assert placed == expected


def test_machine_path_is_absolute_with_backslashes_from_c() -> None:
	assert MachinePath.parse("Tests/assets").full_path == "C:\\Tests\\assets"
	assert MachinePath.parse("d:/data/../logs/").full_path == "D:\\logs"
	assert MachinePath.parse("C:/").full_path == "C:\\"


def test_machine_path_ending_in_slash_dot_asks_for_a_folders_contents() -> None:
	path = MachinePath.parse("C:/Tests/results/.")

	assert path.names_folder
	assert path.archive_path == "C:\\Tests\\results\\."
	assert (path.name, path.parent) == ("results", "C:\\Tests")


# Examples of misuse


def test_file_whose_path_ends_in_a_separator_is_refused() -> None:
	with pytest.raises(NotADirectoryError, match="not a directory"):
		place(
			source_is_folder=False,
			source_names_folder=True,
			source_contents_only=False,
			destination_kind=None,
			destination_names_folder=False,
			destination_name="x",
		)


def test_file_to_a_missing_folder_is_refused() -> None:
	with pytest.raises(FileNotFoundError, match="must exist"):
		place(
			source_is_folder=False,
			source_names_folder=False,
			source_contents_only=False,
			destination_kind=None,
			destination_names_folder=True,
			destination_name="assets",
		)


def test_folder_onto_a_file_is_refused() -> None:
	with pytest.raises(NotADirectoryError, match="cannot copy a directory to a file"):
		place(
			source_is_folder=True,
			source_names_folder=False,
			source_contents_only=False,
			destination_kind=PathMode.FILE,
			destination_names_folder=False,
			destination_name="app.msi",
		)
