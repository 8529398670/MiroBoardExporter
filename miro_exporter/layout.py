"""Write one BoardDump into a snapshot folder, with items nested by frame.

snapshot/
  board.json
  frames/<NN title__id>/frame.json
  frames/<NN title__id>/<type>/<id>.json (+ asset hardlinks, doc .md/.html)
  frames/<NN title__id>/<NN nested title__id>/...
  _unframed/<type>/<id>.json
  connectors.json tags.json groups.json members.json
  index/items.jsonl index/frame_tree.json index/item_tags.json
  index/assets.jsonl            the snapshot's download plan, worked through by attach_assets()
  raw/<collection>/page-0001.json
  errors.jsonl

Writing happens in two steps so every board's metadata can be saved before any (slow)
file downloads: write() lays out all JSON, attach_assets() later links files in.
"""

import json
from collections import Counter
from pathlib import Path, PurePosixPath

from .assets import link_or_copy
from .index import build_frame_tree, compute_geometry, nearest_frame, parent_id
from .util import safe_name, write_json, write_jsonl, write_text

FRAMES_DIR = "frames"
UNFRAMED_DIR = "_unframed"
INDEX_DIR = "index"
RAW_DIR = "raw"
FRAME_FILE = "frame.json"
ITEMS_INDEX = f"{INDEX_DIR}/items.jsonl"
ASSET_PLAN = f"{INDEX_DIR}/assets.jsonl"
COLLECTION_FILES = ("connectors", "groups", "tags", "members")

# Suffix between the item id and the file extension, per asset role.
ASSET_SUFFIX = {"image": "", "document": "", "preview": ".preview"}
DOC_EXTENSIONS = {"markdown": ".md", "html": ".html"}


def _posix(path):
	return PurePosixPath(*Path(path).parts).as_posix()


def read_jsonl(path):
	path = Path(path)
	if not path.is_file():
		return []
	with open(path, encoding="utf-8") as f:
		return [json.loads(line) for line in f if line.strip()]


def frame_dirs(items, tree, roots):
	"""{frame_id: relative dir}, numbered in reading order among siblings."""
	dirs = {}

	def assign(frame_ids, base):
		width = max(2, len(str(len(frame_ids))))
		for n, fid in enumerate(frame_ids, 1):
			title = safe_name(((items[fid].get("data") or {}).get("title")), 60, "Frame")
			dirs[fid] = base / f"{n:0{width}d} {title}__{safe_name(fid, 100)}"
			assign(tree[fid]["child_frames"], dirs[fid])

	assign(roots, Path(FRAMES_DIR))
	return dirs


class SnapshotWriter:
	def __init__(self, root, dump):
		self.root = Path(root)
		self.dump = dump

	def write(self, plan=()):
		"""Lay out every item, index and collection. `plan` is the list of AssetRefs to download later."""
		dump = self.dump
		items = dump.items
		geometry = compute_geometry(items)
		tree, roots = build_frame_tree(items, geometry)
		dirs = frame_dirs(items, tree, roots)

		frame_children = {fid: [] for fid in tree}
		json_paths = {}
		rows = []
		by_type = Counter()
		unframed = 0
		for item_id, item in items.items():
			item_type = item.get("type") or "unknown"
			by_type[item_type] += 1
			file_id = safe_name(item_id, 100, "unknown")
			if item_type == "frame":
				frame_id = item_id
				json_rel = dirs[item_id] / FRAME_FILE
			else:
				frame_id = nearest_frame(items, item_id)
				base = dirs[frame_id] if frame_id else Path(UNFRAMED_DIR)
				json_rel = base / safe_name(item_type, 40, "unknown") / f"{file_id}.json"
				if frame_id:
					frame_children[frame_id].append(item_id)
				else:
					unframed += 1
			json_paths[item_id] = json_rel

			doc_paths = {}
			for kind, payload in sorted((dump.docs.get(item_id) or {}).items()):
				content = (payload.get("data") or {}).get("content")
				if isinstance(content, str):
					rel = json_rel.parent / f"{file_id}{DOC_EXTENSIONS.get(kind, '.' + kind)}"
					write_text(self.root / rel, content)
					doc_paths[kind] = _posix(rel)

			geo = geometry.get(item_id) or {}
			bbox = None
			if geo.get("x") is not None:
				bbox = {k: geo[k] for k in ("x", "y", "width", "height")}
			export_meta = {
				"json_path": _posix(json_rel),
				"frame_id": frame_id if item_type != "frame" else tree[item_id]["parent"],
				"frame_path": _posix(json_rel.parent if item_type == "frame" else (dirs[frame_id] if frame_id else UNFRAMED_DIR)),
				"abs_bbox": bbox,
				"abs_center": geo.get("center"),
				"rotation": geo.get("rotation", 0.0),
				"assets": {},  # filled in by attach_assets() once the files are downloaded
				"docs": doc_paths,
				"tags": dump.item_tags.get(item_id, []),
				"source": dump.item_sources.get(item_id),
			}
			write_json(self.root / json_rel, {**item, "_export": export_meta})
			rows.append({
				"id": item_id,
				"type": item_type,
				"parent_id": parent_id(item),
				**{k: export_meta[k] for k in ("frame_id", "json_path", "abs_bbox", "abs_center", "rotation", "assets", "docs", "tags")},
			})

		write_json(self.root / "board.json", dump.board)
		for name in COLLECTION_FILES:
			if name in dump.collections:
				write_json(self.root / f"{name}.json", dump.collections[name])

		write_jsonl(self.root / ITEMS_INDEX, rows)
		write_json(self.root / INDEX_DIR / "frame_tree.json", {
			"roots": roots,
			"frames": {
				fid: {
					"title": (items[fid].get("data") or {}).get("title"),
					"path": _posix(dirs[fid]),
					"parent_frame_id": node["parent"],
					"child_frames": node["child_frames"],
					"children": frame_children[fid],
				}
				for fid, node in tree.items()
			},
		})
		write_json(self.root / INDEX_DIR / "item_tags.json", dump.item_tags)
		write_jsonl(self.root / ASSET_PLAN, [
			{
				"item_id": ref.item_id,
				"role": ref.role,
				"kind": ref.kind,
				"key": ref.key,
				"url": ref.url,
				"title": ref.title,
				"json_path": _posix(json_paths[ref.item_id]),
				# where the file is linked, minus the extension (known only after download)
				"file_stem": _posix(json_paths[ref.item_id].parent / f"{safe_name(ref.item_id, 100, 'unknown')}{ASSET_SUFFIX.get(ref.role, '.' + ref.role)}"),
			}
			for ref in plan
		])

		for name, pages in dump.raw_pages.items():
			for n, page in enumerate(pages, 1):
				write_json(self.root / RAW_DIR / safe_name(name, 60, "raw") / f"page-{n:04d}.json", page)
		write_jsonl(self.root / "errors.jsonl", dump.errors)

		return {
			"items": len(items),
			"items_by_type": dict(by_type.most_common()),
			"frames": len(tree),
			"unframed_items": unframed,
			**{name: len(dump.collections[name]) for name in COLLECTION_FILES if name in dump.collections},
			"tagged_items": len(dump.item_tags),
			"doc_files": sum(len(v) for v in dump.docs.values()),
		}


def attach_assets(root, plan_rows, results):
	"""Link downloaded files next to their items, and record the paths in the item JSON and index.

	Safe to run again on the same snapshot (a resumed download re-links everything it has).
	Returns {item_id: {role: relative path}} for the files that are now in place.
	"""
	root = Path(root)
	linked = {}
	json_paths = {}
	for row in plan_rows:
		result = results.get((row["item_id"], row["role"]))
		if result is None or result.path is None:
			continue
		rel = f"{row['file_stem']}{result.path.suffix}"
		link_or_copy(result.path, root / rel)
		linked.setdefault(row["item_id"], {})[row["role"]] = rel
		json_paths[row["item_id"]] = row["json_path"]

	for item_id, assets in linked.items():
		path = root / json_paths[item_id]
		with open(path, encoding="utf-8") as f:
			item = json.load(f)
		item["_export"]["assets"] = assets
		write_json(path, item)

	rows = read_jsonl(root / ITEMS_INDEX)
	for row in rows:
		if row["id"] in linked:
			row["assets"] = linked[row["id"]]
	write_jsonl(root / ITEMS_INDEX, rows)
	return linked
