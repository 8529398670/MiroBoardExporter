"""Write one BoardDump into its board folder, in place. Every file exists once; one JSON per kind of record.

<Board name>__<board id>/
  board.json   export.json (written by the exporter)
  items.json   every item in listing order: the API payload plus `_export`
  files.json   every downloaded file: where it is, its original name, which items show it
  frames.json  the frame tree, in reading order
  connectors.json tags.json groups.json members.json
  frames/<NN title__id>/<original name>__<resource id>.<ext>   (placed by assets.FilePlacer)
  frames/<NN title__id>/<doc title>__<item id>.md|.html         doc content
  frames/<NN title__id>/<NN nested title__id>/...
  _unframed/...

Writing happens in two steps so every board's metadata can be saved before any (slow) file
downloads: write() saves the JSON and doc content and returns the files.json plan,
FilePlacer.place() then puts the files in.
"""

from collections import Counter
from pathlib import Path, PurePosixPath

from .assets import FRAMES_DIR, UNFRAMED_DIR
from .index import build_frame_tree, compute_geometry, nearest_frame
from .util import read_json, safe_name, write_json, write_text

BOARD_FILE = "board.json"
ITEMS_FILE = "items.json"
FILES_FILE = "files.json"
FRAMES_FILE = "frames.json"
COLLECTION_FILES = ("connectors", "groups", "tags", "members")
DOC_EXTENSIONS = {"markdown": ".md", "html": ".html"}


def _posix(path):
	return PurePosixPath(*Path(path).parts).as_posix()


def read_json_or(path, default):
	path = Path(path)
	if not path.is_file():
		return default
	try:
		return read_json(path)
	except (OSError, ValueError):
		return default


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


def doc_paths(items):
	"""Every doc content file items.json points at (relative to the board folder)."""
	return {path for item in items for path in ((item.get("_export") or {}).get("docs") or {}).values()}


class BoardWriter:
	def __init__(self, root, dump):
		self.root = Path(root)
		self.dump = dump

	def write(self, refs=(), previous_files=None):
		"""Save every item, collection and doc. Returns (counts, files): the files.json plan for
		`refs` (AssetRefs), for the caller to save once it has found the files already on disk.
		Entries keep their `path` and `name` from `previous_files`.
		"""
		dump = self.dump
		items = dump.items
		geometry = compute_geometry(items)
		tree, roots = build_frame_tree(items, geometry)
		dirs = frame_dirs(items, tree, roots)

		frame_children = {fid: [] for fid in tree}
		out = []
		exports = {}
		by_type = Counter()
		unframed = 0
		for item_id, item in items.items():
			item_type = item.get("type") or "unknown"
			by_type[item_type] += 1
			if item_type == "frame":
				frame_id = tree[item_id]["parent"]
				folder = dirs[item_id]
			else:
				frame_id = nearest_frame(items, item_id)
				folder = dirs[frame_id] if frame_id else Path(UNFRAMED_DIR)
				if frame_id:
					frame_children[frame_id].append(item_id)
				else:
					unframed += 1

			docs = {}
			for kind, payload in sorted((dump.docs.get(item_id) or {}).items()):
				content = (payload.get("data") or {}).get("content")
				if isinstance(content, str):
					stem = safe_name((item.get("data") or {}).get("title"), 60, "doc")
					rel = folder / f"{stem}__{safe_name(item_id, 100, 'unknown')}{DOC_EXTENSIONS.get(kind, '.' + kind)}"
					write_text(self.root / rel, content)
					docs[kind] = _posix(rel)

			geo = geometry.get(item_id) or {}
			bbox = None
			if geo.get("x") is not None:
				bbox = {k: geo[k] for k in ("x", "y", "width", "height")}
			exports[item_id] = {
				"frame_id": frame_id,
				"frame_path": _posix(folder),
				"abs_bbox": bbox,
				"abs_center": geo.get("center"),
				"rotation": geo.get("rotation", 0.0),
				"files": {},  # role -> key in files.json
				"docs": docs,
				"tags": dump.item_tags.get(item_id, []),
				"source": dump.item_sources.get(item_id),
			}
			out.append({**item, "_export": exports[item_id]})

		previous = previous_files or {}
		files = {}
		for ref in refs:
			entry = files.get(ref.key)
			if entry is None:
				before = previous.get(ref.key) or {}
				entry = files[ref.key] = {
					"kind": ref.kind,
					"role": ref.role,
					"url": ref.url,
					"title": ref.title,
					"items": [],
					"folder": exports[ref.item_id]["frame_path"],   # where the first item showing it lives
					"name": before.get("name"),
					"path": before.get("path"),
				}
			entry["items"].append([ref.item_id, ref.role])
			exports[ref.item_id]["files"][ref.role] = ref.key

		write_json(self.root / BOARD_FILE, dump.board)
		for name in COLLECTION_FILES:
			if name in dump.collections:
				write_json(self.root / f"{name}.json", dump.collections[name])
		write_json(self.root / ITEMS_FILE, out)
		write_json(self.root / FRAMES_FILE, {
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

		return {
			"items": len(items),
			"items_by_type": dict(by_type.most_common()),
			"frames": len(tree),
			"unframed_items": unframed,
			**{name: len(dump.collections[name]) for name in COLLECTION_FILES if name in dump.collections},
			"tagged_items": len(dump.item_tags),
			"doc_files": sum(len(v) for v in dump.docs.values()),
		}, files
