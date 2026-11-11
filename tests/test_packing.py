"""The archives the SDK sends: a build's context, packed as ``endia build`` packs it, and the body of ``compose up``."""

import json
import tarfile
from pathlib import Path
from typing import IO, Any

import pytest

from endia._fleet_archive import pack_fleet, selected_services
from endia._tar import default_recipe, ignore_file_for, pack_build_context


def names(archive: IO[bytes]) -> list[str]:
	with tarfile.open(fileobj=archive, mode="r") as tar:
		return tar.getnames()


def make(folder: Path, files: dict[str, str]) -> None:
	for name, text in files.items():
		path = folder / name
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(text, encoding="utf-8")


# Examples of use


def test_build_context_leaves_out_what_the_ignore_file_lists_but_keeps_the_recipe_and_the_ignore_file(tmp_path: Path) -> None:
	make(
		tmp_path,
		{
			"Dockerfile": "FROM eastindia/development:latest\n",
			".dockerignore": "Dockerfile\n.dockerignore\nobj\n*.log\n",
			"app/App.exe": "binary",
			"app/obj/cache.bin": "cache",
			"obj/project.assets.json": "{}",
			"setup.log": "log",
		},
	)

	archive, recipe = pack_build_context(str(tmp_path), str(tmp_path / "Dockerfile"))

	with archive:
		assert recipe == "Dockerfile"
		assert names(archive) == [".dockerignore", "Dockerfile", "app", "app/App.exe", "app/obj", "app/obj/cache.bin"]


def test_recipe_with_its_own_ignore_file_uses_it_instead_of_the_roots(tmp_path: Path) -> None:
	make(
		tmp_path,
		{
			"recipes/dotnet.Dockerfile": "FROM eastindia/development:latest\n",
			"recipes/dotnet.Dockerfile.dockerignore": "docs\n",
			".dockerignore": "src\n",
			"src/Program.cs": "class Program {}",
			"docs/guide.md": "# Guide",
		},
	)

	archive, recipe = pack_build_context(str(tmp_path), str(tmp_path / "recipes/dotnet.Dockerfile"))

	with archive:
		assert recipe == "recipes/dotnet.Dockerfile"
		assert "src/Program.cs" in names(archive)
		archive.seek(0)
		assert "docs/guide.md" not in names(archive)


def test_recipe_outside_the_context_is_added_at_the_root_under_a_random_name(tmp_path: Path) -> None:
	make(tmp_path, {"context/app.txt": "app", "Dockerfile": "FROM eastindia/development:latest\n"})

	archive, recipe = pack_build_context(str(tmp_path / "context"), str(tmp_path / "Dockerfile"))

	with archive:
		assert recipe.startswith(".dockerfile.")
		assert len(recipe) == len(".dockerfile.") + 16
		assert names(archive) == ["app.txt", recipe]


@pytest.mark.parametrize(
	("files", "expected"),
	[
		(["Endiafile", "Dockerfile"], "Endiafile"),
		(["Dockerfile"], "Dockerfile"),
		([], "Dockerfile"),
	],
)
def test_build_that_names_no_recipe_reads_the_endiafile_when_the_folder_holds_one(tmp_path: Path, files: list[str], expected: str) -> None:
	make(tmp_path, dict.fromkeys(files, "FROM eastindia/development:latest\n"))

	assert default_recipe(str(tmp_path)) == expected


def test_ignore_files_are_tried_in_the_clis_order(tmp_path: Path) -> None:
	order = ["Dockerfile.endiaignore", "Dockerfile.dockerignore", ".endiaignore", ".dockerignore"]
	make(tmp_path, {"Dockerfile": "FROM eastindia/development:latest\n", **dict.fromkeys(order, "")})
	recipe = str(tmp_path / "Dockerfile")

	chosen = []
	for name in order:
		chosen.append(Path(ignore_file_for(str(tmp_path), recipe)).name)
		(tmp_path / name).unlink()

	assert chosen == order


def test_recipe_outside_the_context_keeps_its_own_endiaignore_under_the_random_name(tmp_path: Path) -> None:
	make(
		tmp_path,
		{
			"context/app.txt": "app",
			"context/secret.txt": "secret",
			"dotnet.Endiafile": "FROM eastindia/development:latest\n",
			"dotnet.Endiafile.endiaignore": "secret.txt\n",
		},
	)

	archive, recipe = pack_build_context(str(tmp_path / "context"), str(tmp_path / "dotnet.Endiafile"))

	with archive:
		assert names(archive) == ["app.txt", recipe, recipe + ".endiaignore"]


def test_fleet_build_that_names_no_recipe_sends_what_endia_build_sends(tmp_path: Path) -> None:
	make(
		tmp_path,
		{
			"lab/Endiafile": "FROM eastindia/development:latest\n",
			"lab/Dockerfile": "FROM eastindia/development:latest\n",
			"lab/Endiafile.endiaignore": "Dockerfile\nnotes.txt\n",
			"lab/.dockerignore": "keep.txt\n",
			"lab/notes.txt": "left out",
			"lab/keep.txt": "sent",
		},
	)
	model = {"name": "shop", "services": {"lab": {"build": {"context": str(tmp_path / "lab")}}}}

	with pack_fleet(model, selected_services(model, [])) as archive:
		assert names(archive) == [
			"compose.json",
			"contexts/lab",
			"contexts/lab/.dockerignore",
			"contexts/lab/Endiafile",
			"contexts/lab/Endiafile.endiaignore",
			"contexts/lab/keep.txt",
		]


def test_fleet_archive_carries_the_model_the_selected_build_contexts_and_every_bind_source(tmp_path: Path) -> None:
	make(tmp_path, {"web/Dockerfile": "FROM eastindia/development:latest\n", "web/site/index.html": "<p>", "data/seed.sql": "select 1;"})
	model = resolved_model(tmp_path)

	with pack_fleet(model, selected_services(model, ["web"])) as archive, tarfile.open(fileobj=archive, mode="r") as tar:
		entries = tar.getnames()
		model_file = tar.extractfile("compose.json")
		assert model_file is not None
		sent = json.loads(model_file.read())

	assert sent == model
	assert entries == [
		"compose.json",
		"contexts/web",
		"contexts/web/Dockerfile",
		"contexts/web/site/index.html",
		"binds/web/0",
		"binds/web/0/seed.sql",
	]


def test_services_selected_by_name_bring_the_services_they_depend_on() -> None:
	model = {
		"services": {"web": {"depends_on": {"api": {"condition": "service_started"}}}, "api": {"depends_on": ["db"]}, "db": {}, "tools": {}}
	}

	assert selected_services(model, ["web"]) == {"web", "api", "db"}
	assert selected_services(model, []) == {"web", "api", "db", "tools"}


# Examples of misuse


def test_model_with_a_volume_in_short_syntax_is_refused(tmp_path: Path) -> None:
	model = {"name": "lab", "services": {"web": {"image": "lab/web:1", "volumes": ["./data:C:/data"]}}}

	with pytest.raises(ValueError, match="short syntax"):
		pack_fleet(model, [])


def test_model_with_a_relative_build_context_is_refused(tmp_path: Path) -> None:
	model = {"name": "lab", "services": {"web": {"build": {"context": "./web"}}}}

	with pytest.raises(ValueError, match="not an absolute path"):
		pack_fleet(model, ["web"])


def test_named_service_the_model_does_not_have_is_refused() -> None:
	with pytest.raises(ValueError, match="no such service: wbe"):
		selected_services({"services": {"web": {}}}, ["wbe"])


def resolved_model(folder: Path) -> dict[str, Any]:
	"""A model as ``endia compose config --format json`` prints it, with a service that builds and a service that does not."""
	return {
		"name": "sdk-python-classroom",
		"services": {
			"web": {
				"build": {"context": str(folder / "web"), "dockerfile": "Dockerfile"},
				"image": "sdk-python-classroom/web:1",
				"volumes": [{"type": "bind", "source": str(folder / "data"), "target": "C:/data"}],
			},
			"tools": {
				"build": {"context": str(folder / "missing-tools"), "dockerfile": "Dockerfile"},
				"image": "sdk-python-classroom/tools:1",
			},
		},
	}
