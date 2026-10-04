"""Turn an exported board into the compact board model the viewer draws (written to data.js).

Keys are short because a big board has ~8k records:

	id t         item id and Miro type
	x y w h r    world box (rebased so the board starts at 0,0) and rotation in degrees
	f            nearest frame id
	ac           1: automatic height (text); y/h are an estimate around the real center
	html         sanitized rich text
	fs fa        font size in world px; fa=1 when Miro auto-fits the text (fs is then our estimate)
	ff fc        font family key and text color
	al va        text align (l c r) and vertical align (t m b)
	bg           fill; bc bw bs border color, width and style
	kind         shape kind
	title n      title (an image's original file name), and a frame's number in reading order
	m            media record (see media.py)
	o            the document's file in the archive, relative to the site folder
	pg pq ext    document page (1-based), set when the page number is a guess, file extension
	url prov ct  embed link, provider and content type
	nc root      mind map node color, root node
	ca cb ma     created date, creator id, modified date

Links are connectors and mind map branches: {id, k: "c"|"m", sh: curved|straight|elbowed,
a/b: [x, y, nx, ny] (end point and the direction the line leaves its item in), c, w, d,
s0/s1 caps, cap: [{html, p}], fs, i0/i1 item ids}.
"""

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from . import geometry
from .pages import number_pages
from .sanitize import plain_lines, plain_text, sanitize

log = logging.getLogger(__name__)

DATA_VERSION = 1

# Miro's named sticky note colors (Web SDK StickyNoteColor).
STICKY_COLORS = {
	"gray": "#f5f6f8", "light_yellow": "#fff9b1", "yellow": "#f5d128", "orange": "#ff9d48",
	"light_green": "#d5f692", "green": "#c9df56", "dark_green": "#93d275", "cyan": "#67c6c0",
	"light_pink": "#ffcee0", "pink": "#ea94bb", "violet": "#c6a2d2", "red": "#f0939d",
	"light_blue": "#a6ccf5", "blue": "#6cd8fa", "dark_blue": "#9ea9ff", "black": "#000000",
}
AUTO_FONT = {None, "", "999", "auto"}
ALIGN = {"left": "l", "center": "c", "right": "r", "justify": "l"}
VALIGN = {"top": "t", "middle": "m", "bottom": "b"}
BORDER_STYLES = {"dashed", "dotted", "dashdot"}
CAPS = {"none", "stealth", "rounded_stealth", "filled_triangle", "triangle", "arrow", "diamond",
	"filled_diamond", "oval", "filled_oval", "erd_one", "erd_many", "erd_only_one", "erd_zero_or_one",
	"erd_one_or_many", "erd_zero_or_many"}

# Kanban cards and table cells come back at exactly the canvas center: the API has no position for them.
UNPLACEABLE = {"card", "table_text", "kanban", "table"}
# Structure, not content: never drawn and not worth reporting as skipped.
SILENT = {"slide_container"}

# Names a pasted or generated image gets: no better than no name at all.
GENERIC_NAME = re.compile(r"(image|img|blob|file|untitled|download)( ?\(\d+\)| copy)?", re.IGNORECASE)

CHAR_WIDTH = 0.55   # average glyph width / font size for the sans fonts Miro uses
LINE_HEIGHT = 1.4


@dataclass
class MediaRequest:
	kind: str               # image | page | doc
	key: str
	path: Path              # the file in the archive: read for previews, linked to as the original
	page: int = 0


@dataclass
class Model:
	board: dict
	records: list = field(default_factory=list)
	links: list = field(default_factory=list)
	unplaced: list = field(default_factory=list)
	frames: list = field(default_factory=list)
	skipped: Counter = field(default_factory=Counter)
	members: dict = field(default_factory=dict)

	@property
	def requests(self):
		for rec in self.records:
			for slot in ("_m", "_o"):
				if slot in rec:
					yield rec[slot]


# --- small converters -------------------------------------------------------

def _num(value, default=None):
	try:
		return float(value)
	except (TypeError, ValueError):
		return default


def _round(value):
	return round(value, 1) if value is not None else None


def _hex_rgb(value):
	text = value.lstrip("#")
	if len(text) in (3, 4):
		text = "".join(c * 2 for c in text)
	return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), (int(text[6:8], 16) / 255 if len(text) == 8 else 1.0)


def css_color(value, opacity=None):
	"""A CSS color from Miro's #rrggbb / #rrggbbaa / name, with an opacity folded in. None if invisible."""
	if value is None:
		return None
	text = str(value).strip()
	if not text or text.lower() == "transparent":
		return None
	if re.fullmatch(r"#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", text):
		r, g, b, a = _hex_rgb(text)
	elif re.fullmatch(r"[a-zA-Z]{3,20}|rgba?\([\d.,%\s]+\)", text):
		return None if opacity is not None and opacity <= 0 else text
	else:
		return None
	if opacity is not None:
		a *= max(0.0, min(1.0, opacity))
	if a <= 0.001:
		return None
	if a >= 0.999:
		return f"#{r:02x}{g:02x}{b:02x}"
	return f"rgba({r},{g},{b},{round(a, 3)})"


def is_dark(color):
	if not color or not color.startswith("#"):
		return False
	r, g, b, _ = _hex_rgb(color)
	return (0.299 * r + 0.587 * g + 0.114 * b) < 110


def estimate_lines(lines, font_size, width):
	per_line = max(1, int(width / max(font_size * CHAR_WIDTH, 1e-6))) if width > 0 else 10 ** 9
	return max(1, sum(max(1, math.ceil(len(line) / per_line)) for line in lines))


def estimate_text_height(item, width):
	style = item.get("style") or {}
	size = _num(style.get("fontSize"), 14.0)
	lines = plain_lines((item.get("data") or {}).get("content"))
	return estimate_lines(lines, size, width) * size * LINE_HEIGHT


def fit_font(lines, width, height, pad=0.1, max_size=None):
	"""Largest font size at which the text fits the box, as Miro's auto-fit text does."""
	inner_w, inner_h = width * (1 - 2 * pad), height * (1 - 2 * pad)
	if inner_w <= 0 or inner_h <= 0:
		return 12.0
	if not lines:
		return round(min(inner_h / LINE_HEIGHT, 48.0), 1)
	longest = max(len(word) for line in lines for word in (line.split() or [""])) or 1
	lo, hi = 1.0, min(max_size or inner_h, inner_h / LINE_HEIGHT)
	if hi <= lo:
		return round(max(hi, 1.0), 1)
	for _ in range(18):
		mid = (lo + hi) / 2
		fits = estimate_lines(lines, mid, inner_w) * mid * LINE_HEIGHT <= inner_h and longest * mid * CHAR_WIDTH <= inner_w
		lo, hi = (mid, hi) if fits else (lo, mid)
	return round(lo, 1)


# --- per-type records --------------------------------------------------------

def _text_style(rec, style, lines, box, *, pad, auto_default=False, default_size=14.0):
	size = style.get("fontSize")
	if (size is None and auto_default) or (size is not None and str(size) in AUTO_FONT):
		rec["fa"] = 1
		rec["fs"] = fit_font(lines, box.w, box.h, pad=pad)
	else:
		rec["fs"] = _num(size, default_size)
	family = re.sub(r"[^a-z0-9_]", "", str(style.get("fontFamily") or "").lower())
	if family:
		rec["ff"] = family
	color = css_color(style.get("color"))
	if color:
		rec["fc"] = color
	if style.get("textAlign") in ALIGN:
		rec["al"] = ALIGN[style["textAlign"]]
	if style.get("textAlignVertical") in VALIGN:
		rec["va"] = VALIGN[style["textAlignVertical"]]


def _content(item):
	return (item.get("data") or {}).get("content") or ""


def norm_frame(item, rec, box, ctx):
	rec["title"] = plain_text((item.get("data") or {}).get("title")) or ""
	fill = css_color((item.get("style") or {}).get("fillColor"))
	if fill and fill != "#ffffff":
		rec["bg"] = fill


def norm_sticky(item, rec, box, ctx):
	style = item.get("style") or {}
	fill = STICKY_COLORS.get(style.get("fillColor")) or css_color(style.get("fillColor")) or STICKY_COLORS["light_yellow"]
	rec["bg"] = fill
	content = _content(item)
	rec["html"] = sanitize(content)
	_text_style(rec, style, plain_lines(content), box, pad=0.08, auto_default=True)
	if "fc" not in rec and is_dark(fill):
		rec["fc"] = "#ffffff"
	rec.setdefault("al", "c")
	rec.setdefault("va", "m")


def norm_shape(item, rec, box, ctx):
	data, style = item.get("data") or {}, item.get("style") or {}
	rec["kind"] = str(data.get("shape") or "rectangle")
	content = data.get("content") or ""
	if content:
		rec["html"] = sanitize(content)
	fill = css_color(style.get("fillColor"), _num(style.get("fillOpacity"), 1.0))
	if fill:
		rec["bg"] = fill
	width = _num(style.get("borderWidth"), 0.0)
	border = css_color(style.get("borderColor"), _num(style.get("borderOpacity"), 1.0))
	if border and width > 0:
		rec["bc"], rec["bw"] = border, width
		if style.get("borderStyle") in BORDER_STYLES:
			rec["bs"] = style["borderStyle"]
	_text_style(rec, style, plain_lines(content), box, pad=0.08, auto_default=True)
	rec.setdefault("al", "c")
	rec.setdefault("va", "m")


def norm_text(item, rec, box, ctx):
	style = item.get("style") or {}
	rec["html"] = sanitize(_content(item))
	rec["ac"] = 1
	fill = css_color(style.get("fillColor"), _num(style.get("fillOpacity"), 1.0))
	if fill:
		rec["bg"] = fill
	_text_style(rec, style, [], box, pad=0)


def norm_card(item, rec, box, ctx):
	data, style = item.get("data") or {}, item.get("style") or {}
	rec["title"] = plain_text(data.get("title")) or ""
	if data.get("description"):
		rec["html"] = sanitize(data["description"])
	theme = css_color(style.get("cardTheme"))
	if theme:
		rec["bc"] = theme


def norm_mindmap(item, rec, box, ctx):
	data, style = item.get("data") or {}, item.get("style") or {}
	view = data.get("nodeView") or {}
	content = (view.get("data") or {}).get("content") or ""
	rec["html"] = sanitize(content)
	view_style = view.get("style") or {}
	rec["fs"] = _num(style.get("fontSize") or view_style.get("fontSize"), 14.0)
	color = css_color(view_style.get("color"))
	if color:
		rec["fc"] = color
	node_color = css_color(style.get("nodeColor"))
	if node_color:
		rec["nc"] = node_color
	fill = css_color(view_style.get("fillColor"), _num(view_style.get("fillOpacity"), 1.0))
	if fill:
		rec["bg"] = fill
	if data.get("isRoot"):
		rec["root"] = 1


def file_title(name):
	"""An uploaded file's name as a title, unless it's one every pasted image gets ("image.png")."""
	if not name or GENERIC_NAME.fullmatch(PurePosixPath(name).stem.strip()):
		return ""
	return name


def norm_image(item, rec, box, ctx):
	asset = ctx.asset(item, "image")
	if asset:
		rec["_m"] = MediaRequest("image", asset.key, asset.path)
		title = file_title(asset.name)
		if title:
			rec["title"] = title


def norm_document(item, rec, box, ctx):
	data = item.get("data") or {}
	rec["title"] = plain_text(data.get("title")) or ""
	asset = ctx.asset(item, "document")
	if not asset:
		return
	rec["ext"] = asset.path.suffix.lower().lstrip(".")
	page, guessed = ctx.pages.get(str(item["id"]), (0, False))
	rec["pg"] = page + 1
	if guessed:
		rec["pq"] = 1
	if rec["ext"] == "pdf" and ctx.can_render(asset.key, asset.path):
		rec["_m"] = MediaRequest("page", asset.key, asset.path, page=page)
	rec["_o"] = MediaRequest("doc", asset.key, asset.path)


def norm_embed(item, rec, box, ctx):
	data = item.get("data") or {}
	rec["title"] = unquote(str(data.get("title") or "")).strip()
	url = str(data.get("url") or "")
	if url.startswith(("http://", "https://")):
		rec["url"] = url
	if data.get("providerName"):
		rec["prov"] = str(data["providerName"])
	if data.get("contentType"):
		rec["ct"] = str(data["contentType"])
	asset = ctx.asset(item, "preview")
	if asset:
		rec["_m"] = MediaRequest("image", asset.key, asset.path)


def norm_other(item, rec, box, ctx):
	"""Types the API can't describe (tables, kanban, paint, ...): a labelled placeholder."""
	data = item.get("data") or {}
	title = plain_text(data.get("title") or data.get("content") or "")
	if title:
		rec["title"] = title[:200]


NORMALIZERS = {
	"frame": norm_frame,
	"sticky_note": norm_sticky,
	"shape": norm_shape,
	"text": norm_text,
	"card": norm_card,
	"app_card": norm_card,
	"mindmap_node": norm_mindmap,
	"image": norm_image,
	"document": norm_document,
	"embed": norm_embed,
}


# --- links -------------------------------------------------------------------

def _pt(anchor):
	return [anchor[0], anchor[1], round(anchor[2], 4), round(anchor[3], 4)]


def connector_links(connectors, boxes, skipped):
	links = []
	for conn in connectors:
		start, end = conn.get("startItem") or {}, conn.get("endItem") or {}
		a_box, b_box = boxes.get(str(start.get("id"))), boxes.get(str(end.get("id")))
		if a_box is None or b_box is None:
			skipped["connector"] += 1
			continue
		a = geometry.anchor(a_box, start.get("position"), (b_box.cx, b_box.cy))
		b = geometry.anchor(b_box, end.get("position"), (a_box.cx, a_box.cy))
		style = conn.get("style") or {}
		link = {
			"id": str(conn["id"]), "k": "c",
			"sh": conn.get("shape") if conn.get("shape") in ("curved", "straight", "elbowed") else "straight",
			"a": _pt(a), "b": _pt(b),
			"c": css_color(style.get("strokeColor")) or "#1a1a1a",
			"w": _num(style.get("strokeWidth"), 2.0),
			"i0": str(start.get("id")), "i1": str(end.get("id")),
		}
		if style.get("strokeStyle") in ("dashed", "dotted"):
			link["d"] = style["strokeStyle"]
		for slot, cap in (("s0", style.get("startStrokeCap")), ("s1", style.get("endStrokeCap"))):
			if cap and cap != "none" and cap in CAPS:
				link[slot] = cap
		captions = []
		for caption in conn.get("captions") or []:
			text = sanitize(caption.get("content"))
			if text:
				position = _num(str(caption.get("position") or "50%").rstrip("%"), 50.0)
				captions.append({"html": text, "p": max(0.0, min(1.0, position / 100))})
		if captions:
			link["cap"] = captions
			link["fs"] = _num(style.get("fontSize"), 14.0)
			color = css_color(style.get("color"))
			if color:
				link["fc"] = color
		links.append(link)
	return links


def _branch_color(node, by_id):
	"""Miro's nodeColor is a node's fill; branches take the color of the nearest colored ancestor (the root's)."""
	seen = set()
	while node and str(node["id"]) not in seen:
		seen.add(str(node["id"]))
		color = css_color((node.get("style") or {}).get("nodeColor"))
		if color and color not in ("#ffffff", "#fff"):
			return color
		parent = by_id.get(geometry.parent_id(node))
		node = parent if parent and parent.get("type") == "mindmap_node" else None
	return "#8a8a8a"


def mindmap_links(items, by_id, boxes):
	links = []
	for item in items:
		if item.get("type") != "mindmap_node":
			continue
		node_id, pid = str(item["id"]), geometry.parent_id(item)
		parent = by_id.get(pid)
		if not parent or parent.get("type") != "mindmap_node" or node_id not in boxes or pid not in boxes:
			continue
		vertical = (parent.get("data") or {}).get("orientation") == "vertical" or (item.get("data") or {}).get("orientation") == "vertical"
		a, b = geometry.mindmap_edge(boxes[pid], boxes[node_id], vertical)
		color = _branch_color(parent, by_id)
		links.append({"id": f"mm-{node_id}", "k": "m", "sh": "curved", "a": _pt(a), "b": _pt(b), "c": color, "w": 2.0, "i0": pid, "i1": node_id})
	return links


# --- the model -----------------------------------------------------------------

class _Context:
	def __init__(self, snap, can_render):
		self.snap = snap
		self.pages = {}
		self._can_render = can_render

	def asset(self, item, role):
		return (self.snap.assets.get(str(item["id"])) or {}).get(role)

	def can_render(self, key, path):
		return bool(self._can_render and self._can_render(key, path))


def _at_canvas_origin(item):
	pos = item.get("position") or {}
	return geometry.parent_id(item) is None and pos.get("relativeTo", "canvas_center") == "canvas_center" \
		and _num(pos.get("x")) == 0 and _num(pos.get("y")) == 0


def build_model(snap, page_count=None, can_render=None):
	"""Records with absolute coordinates; media still unresolved (`_m`/`_o` requests)."""
	items = snap.items
	by_id = {str(i["id"]): i for i in items}
	boxes = geometry.item_boxes(items, estimate_text_height)
	geometry.recenter_mindmaps(by_id, boxes)
	geometry.layout_slides(items, boxes, snap.frame_order)

	model = Model(board={
		"id": snap.board_id,
		"name": snap.name,
		"viewLink": snap.board.get("viewLink") or (snap.manifest.get("board") or {}).get("view_link"),
		"modifiedAt": snap.board.get("modifiedAt") or (snap.manifest.get("board") or {}).get("modified_at"),
		"exported": snap.manifest.get("finished_at") or snap.manifest.get("surveyed_at"),
	})
	for item in items:
		item_type = str(item.get("type") or "unknown")
		if item_type in UNPLACEABLE and _at_canvas_origin(item):
			title = plain_text((item.get("data") or {}).get("title") or (item.get("data") or {}).get("content"))
			model.unplaced.append({"id": str(item["id"]), "t": item_type, "title": title})
			boxes.pop(str(item["id"]), None)

	ctx = _Context(snap, can_render)
	docs = []
	for item in items:
		asset = ctx.asset(item, "document")
		if item.get("type") == "document" and asset:
			docs.append((item, asset.key))
	keys_to_path = {key: ctx.asset(item, "document").path for item, key in docs}
	ctx.pages = number_pages(docs, boxes, lambda key: page_count(key, keys_to_path[key]) if page_count else None)

	frame_number = {fid: n + 1 for n, fid in enumerate(snap.frame_order)}
	unplaced_ids = {u["id"] for u in model.unplaced}
	frames, others = [], []
	for item in items:
		item_id, item_type = str(item["id"]), str(item.get("type") or "unknown")
		if item_id in unplaced_ids:
			continue
		box = boxes.get(item_id)
		if box is None or (not box.sized and not (item_type == "text" and box.w > 0)):
			if item_type not in SILENT:
				model.skipped[item_type] += 1
			continue
		rec = {"id": item_id, "t": item_type, "x": box.x, "y": box.y, "w": box.w, "h": box.h}
		if box.r:
			rec["r"] = round(box.r, 2)
		frame_id = (item.get("_export") or {}).get("frame_id")
		if frame_id:
			rec["f"] = str(frame_id)
		created_by = (item.get("createdBy") or {}).get("id")
		if item.get("createdAt"):
			rec["ca"] = str(item["createdAt"])[:10]
		if created_by:
			rec["cb"] = str(created_by)
			if str(created_by) in snap.members:
				model.members[str(created_by)] = snap.members[str(created_by)]
		if item.get("modifiedAt") and str(item["modifiedAt"])[:10] != rec.get("ca"):
			rec["ma"] = str(item["modifiedAt"])[:10]
		NORMALIZERS.get(item_type, norm_other)(item, rec, box, ctx)
		if item_type == "frame":
			rec["n"] = frame_number.get(item_id, 0)
			frames.append(rec)
		else:
			others.append(rec)

	frames.sort(key=lambda r: -(r["w"] * r["h"]))   # nested (smaller) frames above their parents
	model.records = frames + others
	drawn = {r["id"] for r in frames}
	model.frames = [fid for fid in snap.frame_order if fid in drawn]
	model.links = mindmap_links(items, by_id, boxes) + connector_links(snap.connectors, boxes, model.skipped)
	return model


def finish(model, resolve):
	"""Resolve media with resolve(request) and rebase everything so the board starts at (0, 0)."""
	for rec in model.records:
		request = rec.pop("_m", None)
		if request is not None:
			media = resolve(request)
			if media:
				rec["m"] = media
		request = rec.pop("_o", None)
		if request is not None:
			path = resolve(request)
			if path:
				rec["o"] = path

	rects = [geometry.Box(r["x"], r["y"], r["w"], r["h"], r.get("r", 0.0)).aabb() for r in model.records]
	rects += [(p[0], p[1], p[0], p[1]) for link in model.links for p in (link["a"], link["b"])]
	bounds = geometry.union(rects) or (0.0, 0.0, 0.0, 0.0)
	ox, oy = math.floor(bounds[0]), math.floor(bounds[1])
	for rec in model.records:
		rec["x"], rec["y"] = _round(rec["x"] - ox), _round(rec["y"] - oy)
		rec["w"], rec["h"] = _round(rec["w"]), _round(rec["h"])
	for link in model.links:
		for end in ("a", "b"):
			p = link[end]
			link[end] = [_round(p[0] - ox), _round(p[1] - oy), p[2], p[3]]

	counts = Counter(r["t"] for r in model.records)
	return {
		"v": DATA_VERSION,
		"board": {**model.board, "items": len(model.records), "counts": dict(counts)},
		"origin": [ox, oy],
		"bounds": [0, 0, _round(bounds[2] - ox), _round(bounds[3] - oy)],
		"skipped": dict(model.skipped),
		"members": model.members,
		"frames": model.frames,
		"items": model.records,
		"links": model.links,
		"unplaced": model.unplaced,
		"media": "media/",
	}
