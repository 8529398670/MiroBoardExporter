import json
import os
from pathlib import Path

import pytest

from miro_exporter import cli
from miro_exporter.client import MiroAPIError
from miro_exporter.exporter import COMPLETE, PENDING, BoardExporter, Survey

from .conftest import BOARD_ID, PDF, PNG


def exporter_for(client, fake, out, **options):
	return BoardExporter(client, out, workers=4, progress=False, download_session=fake, **options)


def run_export(client, fake, out, **options):
	"""Both phases for the test board: returns (survey, download result)."""
	return exporter_for(client, fake, out, **options).export(BOARD_ID)


def load(path):
	return json.loads(path.read_text())


def api_paths(fake):
	return [path for _method, path, _query in fake.calls if path.startswith("api.miro.com")]


def items_by_id(board_dir):
	return {item["id"]: item for item in load(board_dir / "items.json")}


def all_files(board_dir):
	return sorted(p.relative_to(board_dir).as_posix() for p in board_dir.rglob("*") if p.is_file())


F2 = "frames/02 Second Part__F2"


def test_full_export_layout(client, fake, tmp_path):
	survey, result = run_export(client, fake, tmp_path)
	board_dir = tmp_path / "boards" / f"Test Board__{BOARD_ID}"
	assert survey.board_dir == board_dir
	assert result == {"downloaded": 2, "moved": 0, "kept": 0, "failed": 0, "retry_later": 0, "status": COMPLETE}

	export = load(board_dir / "export.json")
	assert export["status"] == COMPLETE
	assert export["schema_version"] == 3
	assert export["board"]["modified_at"] == fake.board_modified
	assert export["counts"]["items"] == 17          # 16 listed items + 1 mind map node
	assert export["counts"]["api_items_total"] == 16
	assert export["counts"]["items_by_type"]["weird_widget"] == 1
	assert export["counts"]["items_by_type"]["table"] == 2
	assert export["files"] == {"planned": 3, "distinct": 2, "missing": 1, "placed": 2, "failed": 0, "bytes_downloaded": len(PNG) + len(PDF)}
	assert set(export["api"]) == {"survey", "files"}

	# One JSON per kind of record, then the files themselves, each exactly once: no links, no copies.
	assert all_files(board_dir) == sorted([
		"board.json", "export.json", "items.json", "files.json", "frames.json",
		"connectors.json", "groups.json", "members.json", "tags.json",
		f"{F2}/Slide1__9001.png", f"{F2}/doc__DF1.md", f"{F2}/doc__DF1.html",
		"_unframed/notes__9002.pdf",
	])
	assert all(os.stat(board_dir / rel).st_nlink == 1 for rel in all_files(board_dir))
	assert (board_dir / F2 / "Slide1__9001.png").read_bytes() == PNG
	assert (board_dir / "_unframed" / "notes__9002.pdf").read_bytes() == PDF
	assert (board_dir / F2 / "doc__DF1.md").read_text() == "# Hello"
	assert (board_dir / F2 / "doc__DF1.html").read_text() == "<h1>Hello</h1>"

	# files.json: where each file is, the name it was uploaded with, and every item showing it.
	files = load(board_dir / "files.json")
	assert set(files) == {"9001", "9002"}
	image = files["9001"]
	assert (image["kind"], image["role"], image["name"], image["path"]) == ("images", "image", "Slide1.jpeg", f"{F2}/Slide1__9001.png")
	assert image["items"] == [["I1", "image"], ["I2", "image"]] and image["folder"] == F2
	assert "format=original" in image["url"]
	assert (files["9002"]["name"], files["9002"]["title"]) == ("notes.pdf", "notes.pdf")

	# items.json holds every item in listing order, with what the exporter worked out in `_export`.
	items = items_by_id(board_dir)
	assert len(items) == 17
	assert items["I1"]["_export"]["files"] == items["I2"]["_export"]["files"] == {"image": "9001"}
	assert items["I0"]["_export"]["files"] == {}
	assert items["DF1"]["_export"]["docs"] == {"markdown": f"{F2}/doc__DF1.md", "html": f"{F2}/doc__DF1.html"}
	assert items["F3"]["_export"]["frame_path"] == "frames/01 Intro__F1/01 Nested__F3" and items["F3"]["_export"]["frame_id"] == "F1"

	# Listing payloads are kept as-is (they already carry style).
	s1 = items["S1"]
	assert s1["style"] == {"fillColor": "#ffffff"}
	assert s1["_export"]["source"] == "list"
	assert s1["_export"]["tags"] == ["T1"]
	assert s1["_export"]["abs_center"] == [-150, -100]
	assert s1["_export"]["frame_path"] == "frames/01 Intro__F1"

	# Flowchart shapes the listing can't represent are filled in from the experimental endpoint.
	assert items["FS1"]["data"]["shape"] == "flow_chart_process" and "style" in items["FS1"]
	assert items["FS2"]["data"]["content"] == "step"
	assert items["W1"]["type"] == "weird_widget" and items["MM1"]["type"] == "mindmap_node"

	# Credits are only spent where the listing is missing something:
	paths = api_paths(fake)
	per_type = [f"/v2/boards/{BOARD_ID}/{kind}/" for kind in ("sticky_notes", "shapes", "frames", "images", "documents")]
	assert not any(prefix in p for p in paths for prefix in per_type)  # the listing already has their data
	assert sum(p.endswith("/items/TB1") for p in paths) == 1   # one probe shows tables have nothing more...
	assert not any(p.endswith("/items/TB2") for p in paths)    # ...so the other table isn't fetched
	assert not any(p.endswith("/items/W1") for p in paths)     # has data already
	assert not any("/resources/images/0" in p for p in paths)  # resource id 0 has no file

	# Board-level collections and the frame tree.
	assert load(board_dir / "connectors.json")[0]["id"] == "C1"
	assert load(board_dir / "tags.json")[0]["title"] == "important"
	assert load(board_dir / "members.json")[0]["id"] == "U1"
	assert load(board_dir / "groups.json") == []
	tree = load(board_dir / "frames.json")
	assert tree["roots"] == ["F1", "F2"]
	assert tree["frames"]["F1"]["child_frames"] == ["F3"]
	assert tree["frames"]["F1"]["children"] == ["S1"]

	# Best-effort gaps are recorded, not fatal.
	assert {e["stage"] for e in export["errors"]} == {"collection:code_widgets", "asset_missing"}
	assert [e["item_id"] for e in export["errors"] if e["stage"] == "asset_missing"] == ["I0"]


def test_survey_saves_all_metadata_and_a_download_plan_before_any_file(client, fake, tmp_path):
	survey = exporter_for(client, fake, tmp_path).survey(BOARD_ID)
	board_dir = survey.board_dir
	assert (survey.status, survey.reused, survey.items) == (PENDING, False, 17)
	assert (survey.planned_files, survey.to_download) == (2, 2)
	assert fake.blob_downloads == 0 and not any("/resources/" in p for p in api_paths(fake))

	export = load(board_dir / "export.json")
	assert export["status"] == PENDING and export["files"] == {"planned": 3, "distinct": 2, "missing": 1}
	assert (board_dir / "items.json").is_file() and (board_dir / "frames.json").is_file()
	assert items_by_id(board_dir)["I1"]["_export"]["files"] == {"image": "9001"}
	files = load(board_dir / "files.json")
	assert {key: (entry["path"], entry["name"]) for key, entry in files.items()} == {"9001": (None, None), "9002": (None, None)}
	assert not list(board_dir.rglob("*.png")) and not list(board_dir.rglob("*.pdf"))


def test_unchanged_board_is_skipped(client, fake, tmp_path):
	run_export(client, fake, tmp_path)
	fake.calls.clear()

	survey, result = run_export(client, fake, tmp_path)
	assert survey.reused and survey.status == COMPLETE
	assert result is None
	assert api_paths(fake) == [f"api.miro.com/v2/boards/{BOARD_ID}"]  # one board lookup, nothing else


def test_board_listing_entry_skips_unchanged_boards_without_any_call(client, fake, tmp_path):
	run_export(client, fake, tmp_path)
	fake.calls.clear()
	listed = {"id": BOARD_ID, "name": "Test Board", "modifiedAt": fake.board_modified}
	survey = exporter_for(client, fake, tmp_path).survey(BOARD_ID, listed)
	assert survey.reused and fake.calls == []


def test_interrupted_downloads_resume_without_refetching_metadata(client, fake, tmp_path):
	fake.flaky_blobs = {"9002"}  # the document's storage download keeps failing this run
	first, result = run_export(client, fake, tmp_path)
	assert result["status"] == PENDING and result["retry_later"] == 1
	export = load(first.board_dir / "export.json")
	assert export["status"] == PENDING and export["files"]["placed"] == 1
	assert [(e["key"], e["items"], e["permanent"]) for e in export["errors"] if e["stage"] == "file"] == [("9002", ["D1"], False)]

	fake.flaky_blobs = set()
	fake.calls.clear()
	survey, result = run_export(client, fake, tmp_path)
	assert survey.reused and survey.to_download == 1
	assert not any(p.endswith("/items") for p in api_paths(fake))  # metadata reused from disk
	assert result == {"downloaded": 1, "moved": 0, "kept": 1, "failed": 0, "retry_later": 0, "status": COMPLETE}
	errors = load(first.board_dir / "export.json")["errors"]
	assert "file" not in {e["stage"] for e in errors}  # the earlier failure was cleared by the retry
	assert (first.board_dir / "_unframed" / "notes__9002.pdf").read_bytes() == PDF


def test_a_file_moved_before_files_json_was_saved_is_found_by_its_id(client, fake, tmp_path):
	first, _ = run_export(client, fake, tmp_path)
	board_dir = first.board_dir
	# As if a run had moved the file and crashed before saving files.json.
	os.replace(board_dir / F2 / "Slide1__9001.png", board_dir / "_unframed" / "Slide1__9001.png")
	downloads = fake.blob_downloads

	fake.board_modified = "2026-08-01T10:00:00Z"
	_, result = run_export(client, fake, tmp_path)
	assert (result["moved"], result["downloaded"]) == (1, 0) and fake.blob_downloads == downloads
	assert (board_dir / F2 / "Slide1__9001.png").read_bytes() == PNG
	assert not (board_dir / "_unframed" / "Slide1__9001.png").exists()


def test_files_the_api_refuses_do_not_hold_the_board_back(client, fake, tmp_path):
	fake.refused_resources = {"9002"}
	survey, result = run_export(client, fake, tmp_path)
	assert result["status"] == COMPLETE and result["failed"] == 1 and result["retry_later"] == 0
	errors = load(survey.board_dir / "export.json")["errors"]
	assert [(e["key"], e["items"], e["permanent"]) for e in errors if e["stage"] == "file"] == [("9002", ["D1"], True)]


def test_a_changed_board_moves_files_instead_of_downloading_them(client, fake, tmp_path):
	first, _ = run_export(client, fake, tmp_path)
	board_dir = first.board_dir
	downloads = fake.blob_downloads

	forced, result = run_export(client, fake, tmp_path, force=True)
	assert not forced.reused and result is None   # nothing to move or fetch: complete straight away
	assert fake.blob_downloads == downloads

	# I1 moves to the Intro frame and the second frame is renamed.
	i1 = next(i for i in fake.items if i["id"] == "I1")
	i1["parent"] = {"id": "F1"}
	next(i for i in fake.items if i["id"] == "F2")["data"]["title"] = "Renamed"
	fake.board_modified = "2026-08-01T10:00:00Z"
	changed, result = run_export(client, fake, tmp_path)
	assert not changed.reused and changed.to_download == 0
	assert result == {"downloaded": 0, "moved": 1, "kept": 1, "failed": 0, "retry_later": 0, "status": COMPLETE}
	assert fake.blob_downloads == downloads
	assert (board_dir / "frames" / "01 Intro__F1" / "Slide1__9001.png").read_bytes() == PNG
	assert (board_dir / "frames" / "02 Renamed__F2" / "doc__DF1.md").is_file()
	assert not (board_dir / F2).exists()          # the old frame folder is gone with what it held
	assert all(os.stat(board_dir / rel).st_nlink == 1 for rel in all_files(board_dir))


def test_files_of_deleted_items_are_removed_and_other_files_are_left_alone(client, fake, tmp_path):
	first, _ = run_export(client, fake, tmp_path)
	board_dir = first.board_dir
	(board_dir / "frames" / "my notes.txt").write_text("mine")

	fake.items = [i for i in fake.items if i["id"] != "D1"]
	fake.board_modified = "2026-08-01T10:00:00Z"
	run_export(client, fake, tmp_path)
	assert not (board_dir / "_unframed").exists()
	assert set(load(board_dir / "files.json")) == {"9001"}
	assert (board_dir / "frames" / "my notes.txt").read_text() == "mine"


def test_renamed_board_keeps_its_folder(client, fake, tmp_path):
	first, _ = run_export(client, fake, tmp_path)
	old_dir = first.board_dir
	renamed = old_dir.with_name(f"Old Name__{BOARD_ID}")
	old_dir.rename(renamed)

	fake.board_modified = "2026-08-01T10:00:00Z"  # renaming a board changes its modifiedAt
	second, result = run_export(client, fake, tmp_path)
	assert not renamed.exists()
	assert second.board_dir == old_dir
	assert result is None and (old_dir / F2 / "Slide1__9001.png").is_file()


def test_a_failed_survey_leaves_the_saved_board_as_it_was(client, fake, tmp_path):
	first, _ = run_export(client, fake, tmp_path)
	board_dir = first.board_dir
	items_before = (board_dir / "items.json").read_text()

	fake.board_modified = "2026-08-01T10:00:00Z"
	fake.fail_items = True
	with pytest.raises(MiroAPIError):
		run_export(client, fake, tmp_path)
	export = load(board_dir / "export.json")
	assert export["status"] == COMPLETE and "500" in export["last_error"]["error"]
	assert (board_dir / "items.json").read_text() == items_before
	assert (board_dir / F2 / "Slide1__9001.png").is_file()

	fake.fail_items = False
	run_export(client, fake, tmp_path)
	export = load(board_dir / "export.json")
	assert export["status"] == COMPLETE and "last_error" not in export
	assert export["board"]["modified_at"] == "2026-08-01T10:00:00Z"


def test_the_old_snapshot_layout_is_converted_by_moving_its_files(client, fake, tmp_path):
	board_dir = tmp_path / "boards" / f"Test Board__{BOARD_ID}"
	store = board_dir / "assets"
	snap = board_dir / "snapshots" / "2026-01-01T000000Z"
	(store / "images").mkdir(parents=True)
	(store / "documents").mkdir()
	(store / "images" / "9001.png").write_bytes(PNG)
	(store / "documents" / "9002.pdf").write_bytes(PDF)
	(snap / "_unframed" / "image").mkdir(parents=True)
	os.link(store / "images" / "9001.png", snap / "_unframed" / "image" / "I2.png")
	(snap / "manifest.json").write_text(json.dumps({"schema_version": 2, "status": "complete"}))
	os.symlink("snapshots/2026-01-01T000000Z", board_dir / "latest", target_is_directory=True)
	# Names looked up beforehand (as fix.sh does) are picked up from files.json.
	(board_dir / "files.json").write_text(json.dumps({"9001": {"name": "Slide1.jpeg"}}))

	survey, result = run_export(client, fake, tmp_path)
	assert survey.to_download == 0
	assert result == {"downloaded": 0, "moved": 2, "kept": 0, "failed": 0, "retry_later": 0, "status": COMPLETE}
	assert fake.blob_downloads == 0 and not any("/resources/" in p for p in api_paths(fake))
	for gone in ("assets", "snapshots", "latest"):
		assert not os.path.lexists(board_dir / gone)
	assert (board_dir / F2 / "Slide1__9001.png").read_bytes() == PNG
	assert (board_dir / "_unframed" / "notes__9002.pdf").read_bytes() == PDF
	assert all(os.stat(board_dir / rel).st_nlink == 1 for rel in all_files(board_dir))


def test_cli_surveys_every_board_before_downloading_any_files(monkeypatch, client):
	calls = []

	class RecordingExporter:
		def __init__(self, *args, **kwargs):
			pass

		def survey(self, board_id, listed, progress=None):
			calls.append(("survey", board_id))
			return Survey(board_id, board_id, Path("."), PENDING, False, 1, 1, 1)

		def download(self, survey):
			calls.append(("download", survey.board_id))
			return {"downloaded": 1, "moved": 0, "kept": 0, "failed": 0, "retry_later": 0, "status": COMPLETE}

	monkeypatch.setattr(cli, "BoardExporter", RecordingExporter)
	monkeypatch.setattr(cli, "list_boards", lambda client, **filters: [{"id": "A"}, {"id": "B"}, {"id": "C"}])
	args = cli.build_parser().parse_args(["export"])
	assert cli.cmd_export(client, args) == 0
	assert sorted(calls[:3]) == [("survey", "A"), ("survey", "B"), ("survey", "C")]  # surveyed in parallel
	assert calls[3:] == [("download", "A"), ("download", "B"), ("download", "C")]     # then files, in board order
