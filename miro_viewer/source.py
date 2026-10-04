"""Read one archived board snapshot exactly as `miro-export` writes it (manifest schema_version 2).

    exports/boards/<Board Name>__<board_id>/latest -> snapshots/<stamp>/
        manifest.json  board.json  connectors.json  members.json
        index/items.jsonl  index/frame_tree.json  index/assets.jsonl
        frames/.../<type>/<id>.json (+ the downloaded file, hard-linked next to it)

Items keep the order of index/items.jsonl. Miro has no z-order field, and that order is the
listing order (ascending ids, i.e. creation order), which is the closest thing to it.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .util import read_json, read_jsonl

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2
BOARDS_DIR = "boards"
LATEST = "latest"


@dataclass
class Asset:
	"""A downloaded file belonging to an item. `key` is Miro's resource id, shared across boards."""
	role: str       # image | document | preview
	kind: str       # images | documents | previews
	key: str
	path: Path      # the file inside the snapshot
	original: Path  # the same file in the board's asset store, which outlives snapshots


@dataclass
class Snapshot:
	board_dir: Path
	root: Path
	board: dict
	manifest: dict
	items: list = field(default_factory=list)
	connectors: list = field(default_factory=list)
	frame_order: list = field(default_factory=list)   # frame ids, reading order, depth-first
	members: dict = field(default_factory=dict)       # member id -> name
	assets: dict = field(default_factory=dict)        # item id -> {role: Asset}

	@property
	def board_id(self):
		return str(self.board.get("id") or (self.manifest.get("board") or {}).get("id") or self.board_dir.name.rsplit("__", 1)[-1])

	@property
	def name(self):
		return self.board.get("name") or (self.manifest.get("board") or {}).get("name") or self.board_dir.name.rsplit("__", 1)[0]


def latest_snapshot(board_dir):
	"""The snapshot `latest` points at, or None. The exporter writes latest.txt where symlinks fail."""
	board_dir = Path(board_dir)
	link = board_dir / LATEST
	if link.is_dir():
		return link.resolve()
	pointer = board_dir / f"{LATEST}.txt"
	if pointer.is_file():
		target = board_dir / pointer.read_text(encoding="utf-8").strip()
		if target.is_dir():
			return target.resolve()
	return None


def find_boards(exports_dir):
	"""Board folders under exports/boards that have a complete snapshot, sorted by name."""
	root = Path(exports_dir) / BOARDS_DIR
	if not root.is_dir():
		return []
	return sorted((d for d in root.iterdir() if d.is_dir() and latest_snapshot(d)), key=lambda d: d.name.lower())


def _optional(path, default):
	return read_json(path) if path.is_file() else default


def _frame_order(tree):
	frames = tree.get("frames") or {}
	order, seen = [], set()

	def visit(fid):
		if fid in seen or fid not in frames:
			return
		seen.add(fid)
		order.append(fid)
		for child in frames[fid].get("child_frames") or []:
			visit(child)

	for fid in tree.get("roots") or []:
		visit(fid)
	order.extend(fid for fid in frames if fid not in seen)
	return order


def load_snapshot(board_dir):
	board_dir = Path(board_dir)
	root = latest_snapshot(board_dir)
	if root is None:
		raise FileNotFoundError(f"{board_dir} has no complete snapshot")
	manifest = _optional(root / "manifest.json", {})
	if manifest.get("schema_version") != SCHEMA_VERSION:
		log.warning("%s: snapshot schema_version %s, the viewer expects %s; output may be incomplete",
			board_dir.name, manifest.get("schema_version"), SCHEMA_VERSION)

	snap = Snapshot(board_dir=board_dir, root=root, board=_optional(root / "board.json", {}), manifest=manifest)
	index = root / "index" / "items.jsonl"
	rows = read_jsonl(index) if index.is_file() else []
	snap.items = [read_json(root / row["json_path"]) for row in rows]
	snap.connectors = _optional(root / "connectors.json", [])
	snap.frame_order = _frame_order(_optional(root / "index" / "frame_tree.json", {}))
	snap.members = {str(m["id"]): m.get("name") for m in _optional(root / "members.json", []) if m.get("id")}

	plan = root / "index" / "assets.jsonl"
	keys = {(str(r["item_id"]), r["role"]): r for r in (read_jsonl(plan) if plan.is_file() else [])}
	for item in snap.items:
		for role, rel in ((item.get("_export") or {}).get("assets") or {}).items():
			path = root / rel
			if not path.is_file():
				continue
			row = keys.get((str(item["id"]), role)) or {}
			key = str(row.get("key") or f"{item['id']}-{role}")
			kind = row.get("kind") or role + "s"
			stored = board_dir / "assets" / kind / f"{key}{path.suffix}"
			snap.assets.setdefault(str(item["id"]), {})[role] = Asset(role, kind, key, path, stored if stored.is_file() else path)
	return snap
