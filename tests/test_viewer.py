"""The viewer builder (miro_viewer), run against snapshots laid out exactly as miro-export writes them."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest

from miro_viewer import geometry, media
from miro_viewer.cli import select_boards
from miro_viewer.geometry import Box
from miro_viewer.normalize import build_model, finish, fit_font
from miro_viewer.pages import number_pages
from miro_viewer.sanitize import plain_lines, plain_text, sanitize
from miro_viewer.site import board_slug, build_site
from miro_viewer.source import find_boards, load_snapshot

ROOT = Path(__file__).resolve().parent.parent


# --- a snapshot on disk, in the exporter's layout ---------------------------------

def item(item_id, item_type, x, y, w=None, h=None, *, parent=None, frame=None, rotation=0.0, data=None, style=None,
		position=None, created="2024-01-01T10:00:00Z", assets=None):
	"""An item as the exporter stores it: the API payload plus `_export` with the absolute top-left box."""
	payload = {"id": item_id, "type": item_type, "createdAt": created, "modifiedAt": created, "createdBy": {"id": "u1"}}
	if data is not None:
		payload["data"] = data
	if style is not None:
		payload["style"] = style
	if w is not None:
		payload["geometry"] = {"width": w, **({"height": h} if h is not None else {}), **({"rotation": rotation} if rotation else {})}
	if parent:
		payload["parent"] = {"id": parent}
	payload["position"] = position or {"x": x, "y": y, "origin": "center", "relativeTo": "canvas_center"}
	payload["_export"] = {
		"frame_id": frame,
		"abs_bbox": {"x": x, "y": y, "width": w, "height": h},
		"rotation": rotation,
		"assets": assets or {},
		"docs": {},
		"tags": [],
	}
	return payload


def write_snapshot(exports, name, board_id, items, *, connectors=(), frames=(), files=None, plan=(), store=None):
	"""files: {snapshot-relative path: bytes}; plan: index/assets.jsonl rows; store: {assets/-relative path: bytes}."""
	board_dir = exports / "boards" / f"{name}__{board_id}"
	snap = board_dir / "snapshots" / "2026-01-01T000000Z"
	(snap / "index").mkdir(parents=True)
	rows = []
	for it in items:
		rel = f"_unframed/{it['type']}/{it['id']}.json"
		it["_export"]["json_path"] = rel
		(snap / rel).parent.mkdir(parents=True, exist_ok=True)
		(snap / rel).write_text(json.dumps(it))
		rows.append({"id": it["id"], "type": it["type"], "json_path": rel})
	(snap / "index" / "items.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
	(snap / "index" / "assets.jsonl").write_text("".join(json.dumps(r) + "\n" for r in plan))
	(snap / "index" / "frame_tree.json").write_text(json.dumps({"roots": list(frames), "frames": {f: {"child_frames": []} for f in frames}}))
	(snap / "manifest.json").write_text(json.dumps({"schema_version": 2, "status": "complete"}))
	(snap / "board.json").write_text(json.dumps({"id": board_id, "name": name, "viewLink": f"https://miro.com/app/board/{board_id}/"}))
	(snap / "connectors.json").write_text(json.dumps(list(connectors)))
	(snap / "members.json").write_text(json.dumps([{"id": "u1", "name": "Ada"}]))
	for root, contents in ((snap, files), (board_dir / "assets", store)):
		for rel, content in (contents or {}).items():
			(root / rel).parent.mkdir(parents=True, exist_ok=True)
			(root / rel).write_bytes(content)
	os.symlink("snapshots/2026-01-01T000000Z", board_dir / "latest", target_is_directory=True)
	return board_dir


def read_data(site, slug):
	text = (site / "boards" / slug / "data.js").read_text()
	assert text.startswith("MV.boot(") and text.rstrip().endswith(");")
	return json.loads(text[len("MV.boot("):text.rstrip().rindex(")")])


# --- sanitize ------------------------------------------------------------------------

def test_sanitize_keeps_quill_lists_and_drops_its_ui_spans():
	html = ('<ol><li data-list="bullet"><span class="ql-ui"><span class="ql-list-ui"></span></span>One</li>'
		'<li class="ql-indent-1" data-list="ordered">Two</li></ol>')
	assert sanitize(html) == '<ol><li data-list="bullet">One</li><li data-list="ordered" class="ql-indent-1">Two</li></ol>'


def test_sanitize_strips_scripts_handlers_and_unsafe_links():
	html = ('<p onclick="x()" style="color: rgb(26, 26, 26); position: fixed">Hi <script>alert(1)</script>'
		'<a href="javascript:alert(1)">bad</a> <a href="https://example.com">good</a><iframe src="x"></iframe>')
	out = sanitize(html)
	assert out == ('<p style="color:rgb(26, 26, 26)">Hi <span>bad</span> '
		'<a href="https://example.com" target="_blank" rel="noopener noreferrer">good</a></p>')


def test_sanitize_escapes_text_and_drops_transparent_backgrounds():
	assert sanitize('<span style="background-color: transparent">a &lt; b & c</span>') == "<span>a &lt; b &amp; c</span>"


def test_plain_text_splits_paragraphs_and_skips_quill_ui():
	html = '<p>Title<br class="softbreak">line</p><ol><li data-list="bullet"><span class="ql-ui"></span>item</li></ol>'
	assert plain_lines(html) == ["Title", "line", "item"]
	assert plain_text("<p>a</p><p>b</p>") == "a b"


# --- geometry ------------------------------------------------------------------------

def test_mindmap_children_are_placed_from_the_parent_center():
	root = item("r", "mindmap_node", 80, 90, 40, 20, data={"isRoot": True})
	child = item("c", "mindmap_node", 0, 0, 30, 10, parent="r",
		position={"x": 50, "y": -10, "origin": "center", "relativeTo": "parent_top_left"})
	grandchild = item("g", "mindmap_node", 0, 0, 10, 10, parent="c",
		position={"x": 20, "y": 0, "origin": "center", "relativeTo": "parent_top_left"})
	items = [root, child, grandchild]
	boxes = geometry.item_boxes(items, lambda *_: 0)
	geometry.recenter_mindmaps({i["id"]: i for i in items}, boxes)
	assert (boxes["c"].cx, boxes["c"].cy) == (150, 90)    # root center (100, 100) + (50, -10)
	assert (boxes["g"].cx, boxes["g"].cy) == (170, 90)


def test_slide_frames_are_spread_into_a_grid_with_their_contents():
	container = item("sc", "slide_container", 0, 0)
	frames = [item(f"f{n}", "frame", 0, 0, 100, 50, parent="sc") for n in range(4)]
	inside = item("doc", "document", 10, 10, 80, 30, parent="f2", frame="f2")
	items = [container, *frames, inside]
	boxes = geometry.item_boxes(items, lambda *_: 0)
	laid = geometry.layout_slides(items, boxes, ["f0", "f1", "f2", "f3"])
	assert laid == {"sc": ["f0", "f1", "f2", "f3"]}
	corners = {(boxes[f"f{n}"].x, boxes[f"f{n}"].y) for n in range(4)}
	assert len(corners) == 4                              # no longer stacked
	assert boxes["doc"].x - boxes["f2"].x == 10 and boxes["doc"].y - boxes["f2"].y == 10


def test_connector_anchor_from_percent_position_and_side():
	box = Box(0, 0, 100, 50)
	assert geometry.anchor(box, {"x": "100%", "y": "50%"}, (500, 0)) == (100, 25, 1.0, 0.0)
	assert geometry.anchor(box, {"x": "50%", "y": "0%"}, (500, 0)) == (50, 0, 0.0, -1.0)


def test_connector_anchor_without_position_meets_the_edge_toward_the_other_end():
	x, y, nx, ny = geometry.anchor(Box(0, 0, 100, 50), None, (50, 500))
	assert (round(x, 6), round(y, 6), nx, ny) == (50, 50, 0.0, 1.0)


def test_connector_anchor_inside_the_box_leaves_by_the_nearest_side():
	x, y, nx, ny = geometry.anchor(Box(0, 0, 100, 100), {"x": "46%", "y": "63%"}, (0, 0))
	assert (x, y) == (46, 63)
	assert (nx, ny) == (0.0, 1.0)                          # 37 from the bottom, 46 from the left


def test_connector_anchor_follows_rotation():
	x, y, nx, ny = geometry.anchor(Box(0, 0, 100, 50, 90), {"x": "100%", "y": "50%"}, (0, 0))
	assert (round(x, 6), round(y, 6)) == (50, 75)
	assert (round(nx, 6), round(ny, 6)) == (0, 1)


# --- pages ---------------------------------------------------------------------------

def _doc(item_id, x, y, created):
	return item(item_id, "document", x, y, 10, 10, created=created)


def test_pages_cover_then_grid():
	cover = _doc("100", 0, 0, "2024-01-01T10:00:00Z")
	grid = [_doc(str(101 + n), (n % 3) * 20, 100 + (n // 3) * 20, "2024-01-01T10:05:00Z") for n in range(6)]
	docs = [(d, "k") for d in [cover, *grid]]
	pages = number_pages(docs, geometry.item_boxes([cover, *grid], lambda *_: 0), lambda key: 6)
	assert pages["100"] == (0, False)
	assert [pages[str(101 + n)][0] for n in range(6)] == [0, 1, 2, 3, 4, 5]


def test_pages_lone_first_row_in_the_same_batch_is_the_cover():
	items = [_doc("1", 0, 0, "2024-01-01T10:00:00Z")]
	items += [_doc(str(2 + n), n * 20, 100, "2024-01-01T10:00:01Z") for n in range(3)]
	pages = number_pages([(d, "k") for d in items], geometry.item_boxes(items, lambda *_: 0), lambda key: 3)
	assert [pages[i][0] for i in ("1", "2", "3", "4")] == [0, 0, 1, 2]


def test_pages_two_expansions_and_clamping():
	first = [_doc(str(10 + n), n * 20, 0, "2024-01-01T10:00:00Z") for n in range(4)]
	second = [_doc(str(20 + n), n * 20, 500, "2024-01-02T10:00:00Z") for n in range(4)]
	pages = number_pages([(d, "k") for d in first + second], geometry.item_boxes(first + second, lambda *_: 0), lambda key: 3)
	assert [pages[str(10 + n)] for n in range(4)] == [(0, True), (1, True), (2, True), (0, True)]  # wraps: batch > pages
	assert [pages[str(20 + n)][0] for n in range(4)] == [0, 1, 2, 0]


# --- normalize -----------------------------------------------------------------------

def test_kanban_cards_at_the_canvas_center_are_listed_not_drawn(tmp_path):
	items = [
		item("c1", "card", 0, 0, 320, 60, data={"title": "<p>Todo</p>"}),
		item("s1", "sticky_note", 100, 100, 100, 100, data={"content": "<p>hi</p>"}, style={"fillColor": "dark_green"}),
		item("p1", "paint", 5, 5),
	]
	snap = load_snapshot(write_snapshot(tmp_path, "B", "b1", items))
	data = finish(build_model(snap), lambda req: None)
	assert data["unplaced"] == [{"id": "c1", "t": "card", "title": "Todo"}]
	assert [r["id"] for r in data["items"]] == ["s1"]
	assert data["skipped"] == {"paint": 1}
	note = data["items"][0]
	assert note["bg"] == "#93d275" and note["fa"] == 1 and (note["x"], note["y"]) == (0, 0)   # rebased to 0,0
	assert data["origin"] == [100, 100]


def test_auto_font_shrinks_for_long_text():
	assert fit_font(["Hi"], 100, 100) > fit_font(["a much longer sentence that has to wrap"], 100, 100)


# --- the whole site --------------------------------------------------------------------

def make_png(path, size):
	from PIL import Image
	path.parent.mkdir(parents=True, exist_ok=True)
	Image.new("RGB", size, (200, 30, 30)).save(path)
	return path.read_bytes()


def make_pdf(path, pages):
	pymupdf = pytest.importorskip("pymupdf")
	doc = pymupdf.open()
	for n in range(pages):
		page = doc.new_page(width=200, height=150)
		page.insert_text((20, 70), f"page {n + 1}")
	path.parent.mkdir(parents=True, exist_ok=True)
	doc.save(path)
	return path.read_bytes()


@pytest.fixture
def board(tmp_path):
	pytest.importorskip("PIL")
	pytest.importorskip("pymupdf")
	scratch = tmp_path / "scratch"
	png = make_png(scratch / "a.png", (1500, 1000))
	pdf = make_pdf(scratch / "d.pdf", 2)
	svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
	items = [
		item("f1", "frame", 0, 0, 2000, 2000, data={"title": "Intro & more"}),
		item("i1", "image", 10, 10, 300, 200, frame="f1", parent="f1", assets={"image": "_unframed/image/i1.png"}),
		item("d1", "document", 400, 10, 200, 150, frame="f1", parent="f1", data={"title": "Deck"},
			assets={"document": "_unframed/document/d1.pdf"}, created="2024-01-01T10:00:00Z"),
		item("d2", "document", 400, 300, 200, 150, frame="f1", parent="f1", data={"title": "Deck"},
			assets={"document": "_unframed/document/d2.pdf"}, created="2024-01-02T10:00:00Z"),
		item("t1", "text", 10, 500, 200, None, frame="f1", parent="f1", data={"content": "<p>Hello <b>board</b></p>"},
			style={"fontSize": "24"}),
		item("v1", "image", 700, 700, 100, 100, frame="f1", parent="f1", assets={"image": "_unframed/image/v1.svg"}),
	]
	plan = [
		{"item_id": "i1", "role": "image", "kind": "images", "key": "900"},
		{"item_id": "d1", "role": "document", "kind": "documents", "key": "700"},
		{"item_id": "d2", "role": "document", "kind": "documents", "key": "700"},
		{"item_id": "v1", "role": "image", "kind": "images", "key": "800"},
	]
	connectors = [{"id": "c1", "shape": "curved", "startItem": {"id": "i1"}, "endItem": {"id": "d1", "position": {"x": "0%", "y": "50%"}},
		"style": {"strokeColor": "#ff0000", "strokeWidth": "3", "endStrokeCap": "stealth"}},
		{"id": "c2", "shape": "straight", "style": {}}]
	exports = tmp_path / "exports"
	write_snapshot(exports, "Café, Board & Co", "uX=", items, connectors=connectors, frames=["f1"], plan=plan,
		files={"_unframed/image/i1.png": png, "_unframed/document/d1.pdf": pdf, "_unframed/document/d2.pdf": pdf,
			"_unframed/image/v1.svg": svg},
		store={"images/900.png": png, "documents/700.pdf": pdf, "images/800.svg": svg})
	return exports


def archived(site, href, board_root):
	"""The archive file a site-relative link points at (and that it stays inside the archive)."""
	target = (site / unquote(href)).resolve()
	assert target.is_relative_to(board_root.resolve())
	return target


def test_build_site_writes_pages_data_and_media(board):
	site = board / "site"
	[summary] = build_site(find_boards(board), site, workers=1, progress=False)
	slug = summary["slug"]
	assert slug == "cafe-board-co-uX"
	for rel in ("index.html", "boards.js", f"{slug}.html", "static/js/app.js", "static/css/board.css"):
		assert (site / rel).is_file(), rel
	page = (site / f"{slug}.html").read_text()
	assert '<script src="static/js/app.js"></script>' in page
	assert f'<script src="boards/{slug}/data.js"></script>' in page

	data = read_data(site, slug)
	recs = {r["id"]: r for r in data["items"]}
	assert data["frames"] == ["f1"] and recs["f1"]["title"] == "Intro & more" and recs["f1"]["n"] == 1
	board_root = next((board / "boards").iterdir())
	image = recs["i1"]["m"]
	assert image["t"] == [256, 1024, 2048] and (image["w"], image["h"]) == (1500, 1000)
	for tier in (256, 1024, 2048):
		assert (site / "media" / f"img/900.{tier}.webp").is_file()
	assert archived(site, image["o"], board_root) == board_root / "assets/images/900.png"
	assert not (site / "media" / "orig").exists()            # originals are linked to, never copied
	svg = recs["v1"]["m"]
	assert svg["v"] == 1 and (site / "media/img/800.svg").is_file()
	assert (recs["d1"]["pg"], recs["d2"]["pg"]) == (1, 1)    # two single-item uploads: both covers
	assert recs["d1"]["m"]["k"] == "pages/700-p001" and (site / "media/pages/700-p001.2048.webp").is_file()
	assert archived(site, recs["d1"]["o"], board_root) == board_root / "assets/documents/700.pdf"
	assert recs["t1"]["ac"] == 1 and recs["t1"]["html"] == "<p>Hello <b>board</b></p>"
	assert [link["id"] for link in data["links"]] == ["c1"] and data["skipped"] == {"connector": 1}
	assert data["links"][0]["s1"] == "stealth" and data["links"][0]["c"] == "#ff0000"
	assert data["members"] == {"u1": "Ada"}


def test_rebuild_reuses_previews(board, monkeypatch):
	site = board / "site"
	build_site(find_boards(board), site, workers=1, progress=False)

	def boom(*args):
		raise AssertionError("previews made again")

	monkeypatch.setattr(media, "make_image_previews", boom)
	monkeypatch.setattr(media, "render_pdf_pages", boom)
	build_site(find_boards(board), site, workers=1, progress=False)


def test_small_images_get_a_top_preview_at_their_own_size(tmp_path):
	pytest.importorskip("PIL")
	from PIL import Image
	make_png(tmp_path / "small.png", (300, 200))
	result = media.make_image_previews(str(tmp_path / "small.png"), str(tmp_path / "out" / "k"))
	assert result["t"] == [256, 2048] and (result["w"], result["h"]) == (300, 200)
	with Image.open(tmp_path / "out" / "k.2048.webp") as top:
		assert top.size == (300, 200)                           # never upscaled


def test_leftover_hard_linked_originals_are_removed(board):
	site = board / "site"
	(site / "media" / "orig").mkdir(parents=True)
	(site / "media" / "orig" / "old.png").write_bytes(b"x")
	build_site(find_boards(board), site, workers=1, progress=False)
	assert not (site / "media" / "orig").exists()


def test_a_renamed_board_replaces_its_old_page(board):
	site = board / "site"
	build_site(find_boards(board), site, workers=1, progress=False)
	board_dir = next((board / "boards").iterdir())
	latest = board_dir / "latest" / "board.json"
	meta = json.loads(latest.read_text())
	meta["name"] = "Renamed"
	latest.write_text(json.dumps(meta))
	build_site(find_boards(board), site, workers=1, progress=False)
	assert not (site / "cafe-board-co-uX.html").exists() and (site / "renamed-uX.html").is_file()
	assert "renamed-uX.html" in (site / "boards.js").read_text()


def test_slug_is_ascii_and_keeps_the_id(tmp_path):
	snap = load_snapshot(write_snapshot(tmp_path, "Stuart & Redman, J Physiol, 1992", "o9J_lWUXGkY=", []))
	assert board_slug(snap) == "stuart-redman-j-physiol-1992-o9J-lWUXGkY"


def test_select_boards_by_id_url_folder_or_name(tmp_path):
	dirs = [tmp_path / "Alpha Board__aaa=", tmp_path / "Beta__bbb="]
	assert select_boards(dirs, ["aaa="]) == ([dirs[0]], [])
	assert select_boards(dirs, ["https://miro.com/app/board/bbb=/?share=1"]) == ([dirs[1]], [])
	assert select_boards(dirs, ["alpha"]) == ([dirs[0]], [])
	assert select_boards(dirs, ["nope"]) == ([], ["nope"])


# --- the browser code ------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_javascript_unit_tests():
	tests = sorted(str(p) for p in (ROOT / "tests" / "js").glob("*.test.js"))
	assert tests
	result = subprocess.run(["node", "--test", *tests], cwd=ROOT, capture_output=True, text=True)
	assert result.returncode == 0, result.stdout + result.stderr
