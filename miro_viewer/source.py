"""Read one exported board folder exactly as `miro-export` writes it (export.json schema_version 3).

    exports/boards/<Board Name>__<board_id>/
        export.json  board.json  items.json  files.json  frames.json  connectors.json  members.json
        frames/.../<original name>__<resource id>.<ext>   every downloaded file, stored once

Items keep the order of items.json. Miro has no z-order field, and that order is the listing
order (ascending ids, i.e. creation order), which is the closest thing to it.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .util import read_json

log = logging.getLogger(__name__)

SCHEMA_VERSION = 3
BOARDS_DIR = "boards"
USABLE = ("complete", "files_pending")


@dataclass
class Asset:
	"""A downloaded file belonging to an item. `key` is Miro's resource id, shared across boards."""
	role: str       # image | document | preview
	kind: str       # images | documents | previews
	key: str
	path: Path      # the one copy, in the board folder
	name: str = None  # the name it was uploaded with, when Miro said


@dataclass
class Snapshot:
	"""The board as it was last exported."""
	board_dir: Path
	board: dict
	manifest: dict                                    # export.json
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


def _optional(path, default):
	return read_json(path) if path.is_file() else default


def is_exported(board_dir):
	"""True once the exporter has saved the board's items (its files may still be downloading)."""
	board_dir = Path(board_dir)
	if not (board_dir / "items.json").is_file():
		return False
	try:
		return read_json(board_dir / "export.json").get("status") in USABLE
	except (OSError, ValueError):
		return False


def find_boards(exports_dir):
	"""Exported board folders under exports/boards, sorted by name."""
	root = Path(exports_dir) / BOARDS_DIR
	if not root.is_dir():
		return []
	return sorted((d for d in root.iterdir() if d.is_dir() and is_exported(d)), key=lambda d: d.name.lower())


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
	if not is_exported(board_dir):
		raise FileNotFoundError(f"{board_dir} has no export")
	manifest = _optional(board_dir / "export.json", {})
	if manifest.get("schema_version") != SCHEMA_VERSION:
		log.warning("%s: export schema_version %s, the viewer expects %s; output may be incomplete",
			board_dir.name, manifest.get("schema_version"), SCHEMA_VERSION)

	snap = Snapshot(board_dir=board_dir, board=_optional(board_dir / "board.json", {}), manifest=manifest)
	snap.items = _optional(board_dir / "items.json", [])
	snap.connectors = _optional(board_dir / "connectors.json", [])
	snap.frame_order = _frame_order(_optional(board_dir / "frames.json", {}))
	snap.members = {str(m["id"]): m.get("name") for m in _optional(board_dir / "members.json", []) if m.get("id")}

	files = _optional(board_dir / "files.json", {})
	for item in snap.items:
		for role, key in ((item.get("_export") or {}).get("files") or {}).items():
			entry = files.get(key) or {}
			if not entry.get("path"):
				continue
			path = board_dir / entry["path"]
			if path.is_file():
				snap.assets.setdefault(str(item["id"]), {})[role] = Asset(role, entry.get("kind") or role + "s", str(key), path, entry.get("name"))
	return snap
