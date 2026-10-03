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


def test_full_export_layout(client, fake, tmp_path):
	survey, result = run_export(client, fake, tmp_path)
	board_dir = tmp_path / "boards" / f"Test Board__{BOARD_ID}"
	snap = survey.snapshot
	assert snap.parent == board_dir / "snapshots"
	assert result == {"downloaded": 2, "reused": 1, "failed": 0, "retry_later": 0, "status": COMPLETE}

	manifest = load(snap / "manifest.json")
	assert manifest["status"] == COMPLETE
	assert manifest["schema_version"] == 2
	assert manifest["board"]["modified_at"] == fake.board_modified
	assert manifest["counts"]["items"] == 17          # 16 listed items + 1 mind map node
	assert manifest["counts"]["api_items_total"] == 16
	assert manifest["counts"]["items_by_type"]["weird_widget"] == 1
	assert manifest["counts"]["items_by_type"]["table"] == 2
	assert manifest["assets"] == {"planned": 3, "files": 2, "missing": 1, "linked": 3, "failed": 0, "bytes_downloaded": len(PNG) + len(PDF)}
	assert set(manifest["api"]) == {"survey", "files"}

	# Frames are numbered in reading order and nested frames live inside their parent.
	f1 = snap / "frames" / "01 Intro__F1"
	f2 = snap / "frames" / "02 Second Part__F2"
	f3 = f1 / "01 Nested__F3"
	for frame_dir in (f1, f2, f3):
		assert (frame_dir / "frame.json").is_file()
	assert (f1 / "sticky_note" / "S1.json").is_file()
	assert (f3 / "sticky_note" / "S2.json").is_file()

	# Every type gets a folder, including ones the exporter has never heard of.
	assert (snap / "_unframed" / "weird_widget" / "W1.json").is_file()
	assert (snap / "_unframed" / "shape" / "SH1.json").is_file()
	assert (snap / "_unframed" / "mindmap_node" / "MM1.json").is_file()
	assert (snap / "_unframed" / "table" / "TB2.json").is_file()

	# Listing payloads are kept as-is (they already carry style).
	s1 = load(f1 / "sticky_note" / "S1.json")
	assert s1["style"] == {"fillColor": "#ffffff"}
	assert s1["_export"]["source"] == "list"
	assert s1["_export"]["tags"] == ["T1"]
	assert s1["_export"]["abs_center"] == [-150, -100]

	# Flowchart shapes the listing can't represent are filled in from the experimental endpoint.
	fs1 = load(f2 / "shape" / "FS1.json")
	assert fs1["data"]["shape"] == "flow_chart_process" and "style" in fs1
	assert load(snap / "_unframed" / "shape" / "FS2.json")["data"]["content"] == "step"

	# Credits are only spent where the listing is missing something:
	paths = api_paths(fake)
	per_type = [f"/v2/boards/{BOARD_ID}/{kind}/" for kind in ("sticky_notes", "shapes", "frames", "images", "documents")]
	assert not any(prefix in p for p in paths for prefix in per_type)  # the listing already has their data
	assert sum(p.endswith("/items/TB1") for p in paths) == 1   # one probe shows tables have nothing more...
	assert not any(p.endswith("/items/TB2") for p in paths)    # ...so the other table isn't fetched
	assert not any(p.endswith("/items/W1") for p in paths)     # has data already
	assert not any("/resources/images/0" in p for p in paths)  # resource id 0 has no file

	# Assets are hardlinked next to their item and share one stored copy.
	i1 = load(f2 / "image" / "I1.json")
	assert i1["_export"]["assets"] == {"image": "frames/02 Second Part__F2/image/I1.png"}
	stored = board_dir / "assets" / "images" / "9001.png"
	assert stored.read_bytes() == PNG
	assert os.stat(f2 / "image" / "I1.png").st_ino == os.stat(stored).st_ino
	assert (snap / "_unframed" / "image" / "I2.png").is_file()
	assert (snap / "_unframed" / "document" / "D1.pdf").read_bytes() == PDF
	assert load(snap / "_unframed" / "image" / "I0.json")["_export"]["assets"] == {}

	# Doc format items get their content as markdown and html files.
	assert (f2 / "doc_format" / "DF1.md").read_text() == "# Hello"
	assert (f2 / "doc_format" / "DF1.html").read_text() == "<h1>Hello</h1>"

	# Board-level collections and indexes.
	assert load(snap / "connectors.json")[0]["id"] == "C1"
	assert load(snap / "tags.json")[0]["title"] == "important"
	assert load(snap / "members.json")[0]["id"] == "U1"
	assert load(snap / "groups.json") == []
	rows = [json.loads(line) for line in (snap / "index" / "items.jsonl").read_text().splitlines()]
	assert len(rows) == 17 and all((snap / r["json_path"]).is_file() for r in rows)
	assert next(r for r in rows if r["id"] == "I1")["assets"] == i1["_export"]["assets"]
	tree = load(snap / "index" / "frame_tree.json")
	assert tree["roots"] == ["F1", "F2"]
	assert tree["frames"]["F1"]["child_frames"] == ["F3"]
	assert tree["frames"]["F1"]["children"] == ["S1"]
	assert (snap / "raw" / "items" / "page-0004.json").is_file()  # 16 items at 4 per page

	# Best-effort gaps are recorded, not fatal.
	errors = [json.loads(line) for line in (snap / "errors.jsonl").read_text().splitlines()]
	assert {e["stage"] for e in errors} == {"collection:code_widgets", "asset_missing"}
	assert [e["item_id"] for e in errors if e["stage"] == "asset_missing"] == ["I0"]

	assert (board_dir / "latest").resolve() == snap.resolve()


def test_survey_saves_all_metadata_and_a_download_plan_before_any_file(client, fake, tmp_path):
	survey = exporter_for(client, fake, tmp_path).survey(BOARD_ID)
	snap = survey.snapshot
	assert (survey.status, survey.reused, survey.items) == (PENDING, False, 17)
	assert (survey.planned_files, survey.to_download) == (2, 2)
	assert fake.blob_downloads == 0 and not any("/resources/" in p for p in api_paths(fake))

	manifest = load(snap / "manifest.json")
	assert manifest["status"] == PENDING and manifest["assets"] == {"planned": 3, "files": 2, "missing": 1}
	assert (snap / "index" / "items.jsonl").is_file() and (snap / "index" / "frame_tree.json").is_file()
	assert load(snap / "frames" / "02 Second Part__F2" / "image" / "I1.json")["_export"]["assets"] == {}
	plan = [json.loads(line) for line in (snap / "index" / "assets.jsonl").read_text().splitlines()]
	assert {(row["item_id"], row["key"]) for row in plan} == {("I1", "9001"), ("I2", "9001"), ("D1", "9002")}
	assert next(row for row in plan if row["item_id"] == "I1")["file_stem"] == "frames/02 Second Part__F2/image/I1"
	assert not (survey.board_dir / "latest").exists()  # latest only ever points at complete snapshots


def test_unchanged_board_is_skipped(client, fake, tmp_path):
	first, _ = run_export(client, fake, tmp_path)
	fake.calls.clear()

	survey, result = run_export(client, fake, tmp_path)
	assert survey.reused and survey.status == COMPLETE and survey.snapshot == first.snapshot
	assert result is None
	assert api_paths(fake) == [f"api.miro.com/v2/boards/{BOARD_ID}"]  # one board lookup, nothing else
	assert len(list(first.snapshot.parent.iterdir())) == 1


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
	manifest = load(first.snapshot / "manifest.json")
	assert manifest["status"] == PENDING and manifest["assets"]["linked"] == 2
	assert not (first.board_dir / "latest").exists()

	fake.flaky_blobs = set()
	fake.calls.clear()
	survey, result = run_export(client, fake, tmp_path)
	assert survey.reused and survey.snapshot == first.snapshot and survey.to_download == 1
	assert not any(p.endswith("/items") for p in api_paths(fake))  # metadata reused from disk
	assert result == {"downloaded": 1, "reused": 2, "failed": 0, "retry_later": 0, "status": COMPLETE}
	errors = [json.loads(line) for line in (first.snapshot / "errors.jsonl").read_text().splitlines()]
	assert "asset" not in {e["stage"] for e in errors}  # the earlier failure was cleared by the retry
	assert (first.board_dir / "latest").resolve() == first.snapshot.resolve()


def test_files_the_api_refuses_do_not_hold_the_snapshot_back(client, fake, tmp_path):
	fake.refused_resources = {"9002"}
	survey, result = run_export(client, fake, tmp_path)
	assert result["status"] == COMPLETE and result["failed"] == 1 and result["retry_later"] == 0
	errors = [json.loads(line) for line in (survey.snapshot / "errors.jsonl").read_text().splitlines()]
	assert [(e["item_id"], e["permanent"]) for e in errors if e["stage"] == "asset"] == [("D1", True)]


def test_force_and_changed_boards_get_new_snapshots_reusing_assets(client, fake, tmp_path):
	first, _ = run_export(client, fake, tmp_path)
	downloads = fake.blob_downloads

	forced, _ = run_export(client, fake, tmp_path, force=True)
	assert not forced.reused and forced.snapshot != first.snapshot

	fake.board_modified = "2026-08-01T10:00:00Z"
	changed, result = run_export(client, fake, tmp_path)
	assert not changed.reused and changed.to_download == 0
	assert result["downloaded"] == 0 and result["reused"] == 3
	assert fake.blob_downloads == downloads
	assert (changed.board_dir / "latest").resolve() == changed.snapshot.resolve()


def test_renamed_board_keeps_its_folder(client, fake, tmp_path):
	first, _ = run_export(client, fake, tmp_path)
	old_dir = first.board_dir
	renamed = old_dir.with_name(f"Old Name__{BOARD_ID}")
	old_dir.rename(renamed)

	fake.board_modified = "2026-08-01T10:00:00Z"  # renaming a board changes its modifiedAt
	second, result = run_export(client, fake, tmp_path)
	assert not renamed.exists()
	assert second.board_dir == old_dir
	assert result["reused"] == 3


def test_failed_run_keeps_latest_and_is_cleaned_up_by_the_next_success(client, fake, tmp_path):
	first, _ = run_export(client, fake, tmp_path)
	board_dir = first.board_dir

	fake.board_modified = "2026-08-01T10:00:00Z"
	fake.fail_items = True
	with pytest.raises(MiroAPIError):
		run_export(client, fake, tmp_path)
	snapshots = sorted(p for p in (board_dir / "snapshots").iterdir())
	assert len(snapshots) == 2
	failed = load(snapshots[-1] / "manifest.json")
	assert failed["status"] == "failed" and "500" in failed["error"]
	assert (board_dir / "latest").resolve() == first.snapshot.resolve()

	fake.fail_items = False
	third, _ = run_export(client, fake, tmp_path)
	remaining = sorted(p.name for p in (board_dir / "snapshots").iterdir())
	assert remaining == [first.snapshot.name, third.snapshot.name]
	assert (board_dir / "latest").resolve() == third.snapshot.resolve()


def test_cli_surveys_every_board_before_downloading_any_files(monkeypatch, client):
	calls = []

	class RecordingExporter:
		def __init__(self, *args, **kwargs):
			pass

		def survey(self, board_id, listed, progress=None):
			calls.append(("survey", board_id))
			return Survey(board_id, board_id, Path("."), Path("snap"), PENDING, False, 1, 1, 1)

		def download(self, survey):
			calls.append(("download", survey.board_id))
			return {"downloaded": 1, "reused": 0, "failed": 0, "retry_later": 0, "status": COMPLETE}

	monkeypatch.setattr(cli, "BoardExporter", RecordingExporter)
	monkeypatch.setattr(cli, "list_boards", lambda client, **filters: [{"id": "A"}, {"id": "B"}, {"id": "C"}])
	args = cli.build_parser().parse_args(["export"])
	assert cli.cmd_export(client, args) == 0
	assert sorted(calls[:3]) == [("survey", "A"), ("survey", "B"), ("survey", "C")]  # surveyed in parallel
	assert calls[3:] == [("download", "A"), ("download", "B"), ("download", "C")]     # then files, in board order
