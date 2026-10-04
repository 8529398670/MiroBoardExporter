"""Write the viewer site: shared static files, a page and data.js per board, the media store, an index.

	<out>/
		index.html  boards.js        the board list
		<slug>.html                  one page per board, at the top, so everything it loads is below it
		static/css/  static/js/      shared by every page
		boards/<slug>/data.js        MV.boot({...}): the board model
		boards/<slug>/summary.json   what the board list shows
		media/                       previews (see media.py); originals stay in the archive

The folder is meant to be copied onto a phone and opened from file://. There, module scripts
and fetch() are blocked, and many local-HTML apps only let a page read files below its own
folder; plain <script src> files under the page's folder always work.
"""

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from urllib.parse import quote

from .media import MediaStore
from .normalize import build_model, finish
from .source import load_snapshot
from .util import read_json, script_json, slugify, write_json, write_text

log = logging.getLogger(__name__)

PACKAGE = Path(__file__).parent
STATIC = PACKAGE / "static"
TEMPLATES = PACKAGE / "templates"

BOARD_STYLES = ["css/base.css", "css/board.css", "css/ui.css"]
BOARD_SCRIPTS = [
	"js/util.js", "js/camera.js", "js/lod.js", "js/geometry.js",
	"js/render/registry.js", "js/render/fit-text.js", "js/render/frame.js", "js/render/note.js",
	"js/render/card.js", "js/render/shape.js", "js/render/text.js", "js/render/media.js",
	"js/render/mindmap.js", "js/render/link.js", "js/render/placeholder.js",
	"js/scene.js", "js/gestures.js",
	"js/ui/panels.js", "js/ui/bar.js", "js/ui/frames.js", "js/ui/search.js", "js/ui/sheet.js",
	"js/ui/lightbox.js", "js/ui/hash.js", "js/ui/viewstate.js",
	"js/app.js",
]
INDEX_STYLES = ["css/base.css", "css/index.css"]
INDEX_SCRIPTS = ["js/util.js", "js/index.js"]


def board_slug(snap):
	board_id = "".join(c if c.isascii() and c.isalnum() else "-" for c in snap.board_id).strip("-")
	return f"{slugify(snap.name)}-{board_id or 'board'}"


def _tags(kind, paths, prefix="static/"):
	if kind == "css":
		return "\n".join(f'<link rel="stylesheet" href="{quote(prefix + p)}">' for p in paths)
	return "\n".join(f'<script src="{quote(prefix + p)}"></script>' for p in paths)


def _escape(text):
	return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def render(template, **values):
	return Template((TEMPLATES / template).read_text(encoding="utf-8")).substitute(**values)


def install_static(out):
	"""Replace <out>/static with this version's files (so renamed files never linger)."""
	dest = Path(out) / "static"
	tmp = Path(out) / ".static.tmp"
	if tmp.exists():
		shutil.rmtree(tmp)
	shutil.copytree(STATIC, tmp)
	if dest.exists():
		shutil.rmtree(dest)
	tmp.rename(dest)


def _cover(data):
	"""A small picture for the board list: the largest image (or rendered page) on the board."""
	best, best_area = None, 0.0
	for rec in data["items"]:
		media = rec.get("m") or {}
		if 256 in (media.get("t") or ()) and rec["w"] * rec["h"] > best_area:
			best, best_area = f"media/{media['k']}.256.webp", rec["w"] * rec["h"]
	return best


def build_board(board_dir, out, store):
	snap = load_snapshot(board_dir)
	slug = board_slug(snap)
	model = build_model(snap, page_count=store.page_count, can_render=store.can_render)
	store.process(list(model.requests), label=snap.name[:28])
	data = finish(model, store.resolve)

	write_text(out / "boards" / slug / "data.js", f"MV.boot({script_json(data)});\n")
	write_text(out / f"{slug}.html", render("board.html",
		title=_escape(snap.name),
		styles=_tags("css", BOARD_STYLES),
		scripts=_tags("js", BOARD_SCRIPTS),
		data=quote(f"boards/{slug}/data.js"),
	))
	counts = data["board"]["counts"]
	summary = {
		"id": snap.board_id,
		"name": snap.name,
		"slug": slug,
		"page": quote(f"{slug}.html"),
		"items": data["board"]["items"],
		"frames": len(data["frames"]),
		"images": counts.get("image", 0),
		"documents": counts.get("document", 0),
		"links": len(data["links"]),
		"modifiedAt": data["board"].get("modifiedAt"),
		"exported": data["board"].get("exported"),
		"cover": quote(_cover(data)) if _cover(data) else None,
		"built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
		"skipped": data["skipped"],
		"unplaced": len(data["unplaced"]),
	}
	write_json(out / "boards" / slug / "summary.json", summary)
	_drop_renamed(out, snap.board_id, slug)
	return summary


def _drop_renamed(out, board_id, slug):
	"""A board renamed in Miro gets a new slug; remove the page and data under its old one."""
	for summary_path in (out / "boards").glob("*/summary.json"):
		old = summary_path.parent.name
		if old != slug and read_json(summary_path).get("id") == board_id:
			shutil.rmtree(summary_path.parent, ignore_errors=True)
			(out / f"{old}.html").unlink(missing_ok=True)


def write_index(out):
	summaries = []
	for path in sorted((out / "boards").glob("*/summary.json")):
		summary = read_json(path)
		if (out / f"{summary['slug']}.html").is_file():
			summaries.append(summary)
	summaries.sort(key=lambda s: s["name"].lower())
	write_text(out / "boards.js", f"MV.showBoards({script_json(summaries)});\n")
	write_text(out / "index.html", render("index.html",
		styles=_tags("css", INDEX_STYLES),
		scripts=_tags("js", INDEX_SCRIPTS),
	))
	return summaries


def build_site(board_dirs, out, *, workers=None, force=False, progress=True, on_board=None):
	out = Path(out)
	out.mkdir(parents=True, exist_ok=True)
	install_static(out)
	# Earlier versions hard-linked the originals here; pages now link to the archive instead.
	shutil.rmtree(out / "media" / "orig", ignore_errors=True)
	store = MediaStore(out / "media", workers=workers, force=force, progress=progress)
	built = []
	try:
		for board_dir in board_dirs:
			summary = build_board(Path(board_dir), out, store)
			store.save()
			built.append(summary)
			if on_board:
				on_board(summary)
	finally:
		store.close()
		store.save()
		write_index(out)
	return built
