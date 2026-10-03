"""Board -> timestamped snapshot, in two phases so a multi-board run saves every board's metadata first.

survey():   board info, every item, collections, indexes and a download plan -> snapshot marked
            `assets_pending` (cheap: a few minutes for dozens of boards). Unchanged boards are skipped.
download(): fetch the plan's files into the board's shared asset store, link them into the
            snapshot, mark it `complete` and point `latest` at it (slow: Miro's rate limit).
"""

import logging
import os
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .assets import AssetRef, AssetStore, collect_asset_refs
from .client import Cancelled
from .fetch import BoardFetcher
from .layout import ASSET_PLAN, ITEMS_INDEX, SnapshotWriter, attach_assets, read_jsonl
from .util import read_json, safe_name, utc_now_iso, utc_stamp, write_json, write_jsonl, write_text

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2
BOARDS_DIR = "boards"
SNAPSHOTS_DIR = "snapshots"
ASSETS_DIR = "assets"
LATEST = "latest"

COMPLETE = "complete"
PENDING = "assets_pending"   # metadata saved, files still to download
USABLE = (COMPLETE, PENDING)


def resolve_board_dir(out_dir, board_id, board_name):
	"""exports/boards/<name>__<id>. A renamed board keeps its folder (renamed to match) and its assets."""
	root = Path(out_dir) / BOARDS_DIR
	suffix = f"__{safe_name(board_id, 100, 'board')}"
	wanted = root / f"{safe_name(board_name, 80, 'Untitled board')}{suffix}"
	if root.is_dir() and not wanted.exists():
		for existing in root.iterdir():
			if existing.is_dir() and existing.name.endswith(suffix):
				log.info("Board was renamed, moving %s -> %s", existing.name, wanted.name)
				existing.rename(wanted)
				break
	return wanted


def new_snapshot_dir(board_dir):
	base = Path(board_dir) / SNAPSHOTS_DIR
	stamp = utc_stamp()
	path = base / stamp
	n = 2
	while path.exists():
		path = base / f"{stamp}-{n}"
		n += 1
	path.mkdir(parents=True)
	return path


def point_latest(board_dir, snapshot_dir):
	"""Atomically repoint board_dir/latest at the snapshot (falls back to latest.txt without symlinks)."""
	board_dir = Path(board_dir)
	rel = Path(snapshot_dir).relative_to(board_dir)
	tmp = board_dir / f".{LATEST}.tmp"
	try:
		if tmp.is_symlink() or tmp.exists():
			tmp.unlink()
		os.symlink(rel, tmp, target_is_directory=True)
		os.replace(tmp, board_dir / LATEST)
	except OSError:
		write_text(board_dir / f"{LATEST}.txt", f"{rel.as_posix()}\n")


def read_manifest(snapshot_dir):
	try:
		return read_json(Path(snapshot_dir) / "manifest.json")
	except (OSError, ValueError):
		return None


def newest_snapshot(board_dir, statuses):
	"""(snapshot_dir, manifest) of the newest snapshot whose status is in `statuses`, or None."""
	base = Path(board_dir) / SNAPSHOTS_DIR
	if not base.is_dir():
		return None
	for snap in sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
		manifest = read_manifest(snap)
		if manifest and manifest.get("status") in statuses:
			return snap, manifest
	return None


def latest_complete(board_dir):
	return newest_snapshot(board_dir, (COMPLETE,))


def prune_incomplete(board_dir, keep):
	"""Once `keep` is complete, every other unfinished snapshot of the board is superseded: remove it.
	(Don't run two exports into the same folder at once.)"""
	for snap in (Path(board_dir) / SNAPSHOTS_DIR).iterdir():
		if snap.is_dir() and snap != Path(keep) and (read_manifest(snap) or {}).get("status") != COMPLETE:
			log.debug("Removing superseded snapshot %s", snap.name)
			shutil.rmtree(snap, ignore_errors=True)


@dataclass
class Survey:
	board_id: str
	name: str
	board_dir: Path
	snapshot: Path
	status: str            # complete | assets_pending
	reused: bool           # an existing snapshot already matched the board, no item calls made
	items: int
	planned_files: int     # distinct files the snapshot links to
	to_download: int       # of those, not in the asset store yet


class BoardExporter:
	def __init__(self, client, out_dir, *, workers=8, details=True, asset_format="original", force=False, progress=True, download_session=None):
		self.client = client
		self.out_dir = Path(out_dir)
		self.workers = workers
		self.details = details
		self.asset_format = asset_format
		self.force = force
		self.progress = progress
		self.download_session = download_session

	def asset_store(self, board_dir):
		return AssetStore(Path(board_dir) / ASSETS_DIR, self.client, download_session=self.download_session, workers=self.workers, progress=self.progress)

	def is_current(self, manifest, board):
		"""True if a snapshot already holds this board as it is now, made with the options we'd use.

		Board modifiedAt moves whenever content changes (checked against item timestamps on real
		boards), so an unchanged value means re-fetching would produce the same snapshot.
		"""
		options = manifest.get("options") or {}
		modified = board.get("modifiedAt")
		return bool(
			modified
			and (manifest.get("board") or {}).get("modified_at") == modified
			and manifest.get("schema_version") == SCHEMA_VERSION
			and (options.get("details") or not self.details)
			and options.get("asset_format") == self.asset_format
		)

	def _plan_counts(self, board_dir, snapshot_dir):
		refs = [AssetRef.from_plan(row) for row in read_jsonl(Path(snapshot_dir) / ASSET_PLAN)]
		files = len({(ref.kind, ref.key) for ref in refs})
		return files, self.asset_store(board_dir).count_missing(refs)

	def survey(self, board_id, listed=None, progress=None):
		"""Phase 1. `listed` is the board's entry from the boards listing, if we have it: its
		modifiedAt lets an unchanged board be skipped without any call at all. `progress` overrides
		the per-board progress bars (off when several boards are surveyed at once)."""
		progress = self.progress if progress is None else progress
		fetcher = BoardFetcher(self.client, workers=self.workers, details=self.details, progress=progress)
		board = listed or fetcher.fetch_board(board_id)
		board_dir = resolve_board_dir(self.out_dir, board_id, board.get("name"))

		previous = None if self.force else newest_snapshot(board_dir, USABLE)
		if previous and self.is_current(previous[1], board):
			snap, manifest = previous
			files, missing = self._plan_counts(board_dir, snap) if manifest["status"] == PENDING else (0, 0)
			return Survey(board_id, board.get("name"), board_dir, snap, manifest["status"], True,
				(manifest.get("counts") or {}).get("items", 0), files, missing)

		if listed is not None:
			board = fetcher.fetch_board(board_id)  # the full board object, for board.json
		snapshot_dir = new_snapshot_dir(board_dir)
		manifest = {
			"schema_version": SCHEMA_VERSION,
			"tool": {"name": "miro-exporter", "version": __version__},
			"status": "in_progress",
			"started_at": utc_now_iso(),
			"finished_at": None,
			"board": {
				"id": board_id,
				"name": board.get("name"),
				"view_link": board.get("viewLink"),
				"modified_at": board.get("modifiedAt"),
			},
			"options": {"details": self.details, "asset_format": self.asset_format},
			"paths": {
				"board": "board.json",
				"items_index": ITEMS_INDEX,
				"frame_tree": "index/frame_tree.json",
				"item_tags": "index/item_tags.json",
				"asset_plan": ASSET_PLAN,
				"errors": "errors.jsonl",
				"asset_store": f"../../{ASSETS_DIR}",
			},
		}
		manifest_path = snapshot_dir / "manifest.json"
		write_json(manifest_path, manifest)
		log.debug("Surveying %r into %s", board.get("name"), snapshot_dir)
		api_before = self.client.stats_snapshot()

		def finish(status, **extra):
			api_after = self.client.stats_snapshot()
			manifest.update(status=status, **extra)
			manifest["api"] = {"survey": {k: api_after[k] - api_before.get(k, 0) for k in api_after}}
			write_json(manifest_path, manifest)

		try:
			dump = fetcher.run(board_id, board)
			plan, missing = collect_asset_refs(dump.items, self.asset_format)
			for item_id, role, url in missing:
				dump.add_error("asset_missing", None, item_id=item_id, role=role, url=url, message="item has no stored file (resource id 0)")
			counts = SnapshotWriter(snapshot_dir, dump).write(plan)
			counts["api_items_total"] = dump.items_total
			files = len({(ref.kind, ref.key) for ref in plan})
			status = PENDING if plan else COMPLETE
			finish(status, counts=counts, errors=len(dump.errors), surveyed_at=utc_now_iso(),
				assets={"planned": len(plan), "files": files, "missing": len(missing)},
				finished_at=utc_now_iso() if status == COMPLETE else None)
		except (KeyboardInterrupt, Cancelled):
			finish("interrupted", finished_at=utc_now_iso())
			raise
		except Exception as e:
			finish("failed", error=f"{type(e).__name__}: {e}", finished_at=utc_now_iso())
			raise

		if status == COMPLETE:
			point_latest(board_dir, snapshot_dir)
			prune_incomplete(board_dir, snapshot_dir)
		to_download = self.asset_store(board_dir).count_missing(plan) if plan else 0
		return Survey(board_id, board.get("name"), board_dir, snapshot_dir, status, False, counts["items"], files, to_download)

	def download(self, survey):
		"""Phase 2: get the snapshot's planned files and finish it. Re-running continues where it stopped.

		A snapshot stays `assets_pending` while some file failed for a reason a later run could fix
		(network, server errors); files the API refuses outright are recorded and don't hold it back.
		"""
		snapshot_dir = survey.snapshot
		manifest_path = snapshot_dir / "manifest.json"
		manifest = read_json(manifest_path)
		if manifest.get("status") != PENDING:
			return None

		rows = read_jsonl(snapshot_dir / ASSET_PLAN)
		refs = [AssetRef.from_plan(row) for row in rows]
		api_before = self.client.stats_snapshot()
		results = self.asset_store(survey.board_dir).fetch_all(refs)
		linked = attach_assets(snapshot_dir, rows, results)

		failed = {key: r for key, r in results.items() if r.status == "failed"}
		retry_later = [key for key, r in failed.items() if not r.permanent]
		errors = [e for e in read_jsonl(snapshot_dir / "errors.jsonl") if e.get("stage") != "asset"]
		errors += [
			{"at": utc_now_iso(), "stage": "asset", "item_id": item_id, "role": role, "error": r.error, "permanent": r.permanent}
			for (item_id, role), r in failed.items()
		]
		write_jsonl(snapshot_dir / "errors.jsonl", errors)

		counts = Counter(r.status for r in results.values())
		api_after = self.client.stats_snapshot()
		previous_files_api = manifest.get("api", {}).get("files") or {}
		status = PENDING if retry_later else COMPLETE
		manifest["assets"].update(
			linked=sum(len(roles) for roles in linked.values()),
			failed=len(failed),
			bytes_downloaded=manifest["assets"].get("bytes_downloaded", 0) + sum(r.bytes for r in results.values()),
		)
		manifest.setdefault("api", {})["files"] = {k: api_after[k] - api_before.get(k, 0) + previous_files_api.get(k, 0) for k in api_after}
		manifest.update(status=status, errors=len(errors), finished_at=utc_now_iso() if status == COMPLETE else None)
		write_json(manifest_path, manifest)

		if status == COMPLETE:
			point_latest(survey.board_dir, snapshot_dir)
			prune_incomplete(survey.board_dir, snapshot_dir)
		return {
			"downloaded": counts.get("downloaded", 0),
			"reused": counts.get("reused", 0),
			"failed": len(failed),
			"retry_later": len(retry_later),
			"status": status,
		}

	def export(self, board_id, listed=None):
		"""Both phases for one board (the CLI runs every survey first, then every download)."""
		survey = self.survey(board_id, listed)
		return survey, self.download(survey)
