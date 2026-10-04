"""Board -> its folder under exports/boards, updated in place, in two phases so a multi-board run
saves every board's metadata first.

survey():   board info, every item, collections, frame tree and the files.json plan, written in
            place -> export.json `files_pending` (cheap: a few minutes for dozens of boards).
            Unchanged boards are skipped.
download(): move the files already there into place and download the rest, then remove what the
            board no longer has -> `complete` (slow: Miro's rate limit).
"""

import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .assets import FilePlacer, collect_asset_refs
from .client import Cancelled
from .fetch import BoardFetcher
from .layout import FILES_FILE, ITEMS_FILE, BoardWriter, doc_paths, read_json_or
from .util import safe_name, utc_now_iso, write_json

log = logging.getLogger(__name__)

SCHEMA_VERSION = 3
BOARDS_DIR = "boards"
EXPORT_FILE = "export.json"

COMPLETE = "complete"
PENDING = "files_pending"   # metadata saved, files still to download or move into place
USABLE = (COMPLETE, PENDING)


def resolve_board_dir(out_dir, board_id, board_name):
	"""exports/boards/<name>__<id>. A renamed board keeps its folder (renamed to match) and its files."""
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


def read_export(board_dir):
	return read_json_or(Path(board_dir) / EXPORT_FILE, None)


@dataclass
class Survey:
	board_id: str
	name: str
	board_dir: Path
	status: str            # complete | files_pending
	reused: bool           # the saved export already matched the board, no item calls made
	items: int
	planned_files: int     # distinct files the board shows
	to_download: int       # of those, not on disk yet


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

	def placer(self, board_dir):
		return FilePlacer(board_dir, self.client, download_session=self.download_session, workers=self.workers, progress=self.progress)

	def is_current(self, export, board):
		"""True if the saved export already holds this board as it is now, made with the options we'd use.

		Board modifiedAt moves whenever content changes (checked against item timestamps on real
		boards), so an unchanged value means re-fetching would produce the same export.
		"""
		options = export.get("options") or {}
		modified = board.get("modifiedAt")
		return bool(
			modified
			and export.get("status") in USABLE
			and (export.get("board") or {}).get("modified_at") == modified
			and export.get("schema_version") == SCHEMA_VERSION
			and (options.get("details") or not self.details)
			and options.get("asset_format") == self.asset_format
		)

	def survey(self, board_id, listed=None, progress=None):
		"""Phase 1. `listed` is the board's entry from the boards listing, if we have it: its
		modifiedAt lets an unchanged board be skipped without any call at all. `progress` overrides
		the per-board progress bars (off when several boards are surveyed at once)."""
		progress = self.progress if progress is None else progress
		fetcher = BoardFetcher(self.client, workers=self.workers, details=self.details, progress=progress)
		board = listed or fetcher.fetch_board(board_id)
		board_dir = resolve_board_dir(self.out_dir, board_id, board.get("name"))

		previous = None if self.force else read_export(board_dir)
		if previous and self.is_current(previous, board):
			files = read_json_or(board_dir / FILES_FILE, {})
			missing = len(self.placer(board_dir).missing(files)) if previous["status"] == PENDING else 0
			return Survey(board_id, board.get("name"), board_dir, previous["status"], True,
				(previous.get("counts") or {}).get("items", 0), len(files), missing)

		if listed is not None:
			board = fetcher.fetch_board(board_id)  # the full board object, for board.json
		log.debug("Surveying %r into %s", board.get("name"), board_dir)
		started = utc_now_iso()
		api_before = self.client.stats_snapshot()
		try:
			dump = fetcher.run(board_id, board)
		except (KeyboardInterrupt, Cancelled):
			self._record_failure(board_dir, "interrupted")
			raise
		except Exception as e:
			self._record_failure(board_dir, f"{type(e).__name__}: {e}")
			raise

		plan, missing = collect_asset_refs(dump.items, self.asset_format)
		for item_id, role, url in missing:
			dump.add_error("asset_missing", None, item_id=item_id, role=role, url=url, message="item has no stored file (resource id 0)")
		previous_files = read_json_or(board_dir / FILES_FILE, {})
		counts, files = BoardWriter(board_dir, dump).write(plan, previous_files)
		counts["api_items_total"] = dump.items_total

		placer = self.placer(board_dir)
		status = PENDING if placer.unplaced(files) else COMPLETE   # also finds files already on disk
		write_json(board_dir / FILES_FILE, files)
		api_after = self.client.stats_snapshot()
		export = {
			"schema_version": SCHEMA_VERSION,
			"tool": {"name": "miro-exporter", "version": __version__},
			"status": status,
			"board": {
				"id": board_id,
				"name": board.get("name"),
				"view_link": board.get("viewLink"),
				"modified_at": board.get("modifiedAt"),
			},
			"options": {"details": self.details, "asset_format": self.asset_format},
			"started_at": started,
			"surveyed_at": utc_now_iso(),
			"finished_at": utc_now_iso() if status == COMPLETE else None,
			"counts": counts,
			"files": {"planned": len(plan), "distinct": len(files), "missing": len(missing)},
			"errors": dump.errors,
			"api": {"survey": {k: api_after[k] - api_before.get(k, 0) for k in api_after}},
		}
		write_json(board_dir / EXPORT_FILE, export)
		if status == COMPLETE:
			placer.tidy(self._keep(board_dir, files))
		to_download = sum(1 for entry in files.values() if not entry["path"])
		return Survey(board_id, board.get("name"), board_dir, status, False, counts["items"], len(files), to_download)

	def _record_failure(self, board_dir, error):
		"""A failed survey leaves the board's saved data as it was; export.json just notes the attempt."""
		export = read_export(board_dir)
		if export is not None:
			export["last_error"] = {"at": utc_now_iso(), "error": error}
			write_json(Path(board_dir) / EXPORT_FILE, export)

	def _keep(self, board_dir, files):
		"""Every file the board folder should hold: placed files plus doc content."""
		keep = {entry["path"] for entry in files.values() if entry.get("path")}
		return keep | doc_paths(read_json_or(Path(board_dir) / ITEMS_FILE, []))

	def download(self, survey):
		"""Phase 2: put the board's files in place and finish it. Re-running continues where it stopped.

		A board stays `files_pending` while some file failed for a reason a later run could fix
		(network, server errors); files the API refuses outright are recorded and don't hold it back.
		"""
		board_dir = survey.board_dir
		export = read_export(board_dir)
		if not export or export.get("status") != PENDING:
			return None

		files = read_json_or(board_dir / FILES_FILE, {})
		placer = self.placer(board_dir)
		api_before = self.client.stats_snapshot()
		results = placer.place(files, save=lambda: write_json(board_dir / FILES_FILE, files))

		failed = {key: r for key, r in results.items() if r.status == "failed"}
		retry_later = [key for key, r in failed.items() if not r.permanent]
		errors = [e for e in export.get("errors") or [] if e.get("stage") != "file"]
		errors += [
			{"at": utc_now_iso(), "stage": "file", "key": key, "items": [i for i, _ in files[key]["items"]], "error": r.error, "permanent": r.permanent}
			for key, r in failed.items()
		]

		counts = Counter(r.status for r in results.values())
		api_after = self.client.stats_snapshot()
		previous_files_api = export.get("api", {}).get("files") or {}
		status = PENDING if retry_later else COMPLETE
		export["files"].update(
			placed=sum(1 for entry in files.values() if entry.get("path")),
			failed=len(failed),
			bytes_downloaded=export["files"].get("bytes_downloaded", 0) + sum(r.bytes for r in results.values()),
		)
		export.setdefault("api", {})["files"] = {k: api_after[k] - api_before.get(k, 0) + previous_files_api.get(k, 0) for k in api_after}
		export.update(status=status, errors=errors, finished_at=utc_now_iso() if status == COMPLETE else None)
		write_json(board_dir / EXPORT_FILE, export)

		if status == COMPLETE:
			placer.tidy(self._keep(board_dir, files))
		return {
			"downloaded": counts.get("downloaded", 0),
			"moved": counts.get("moved", 0),
			"kept": counts.get("kept", 0),
			"failed": len(failed),
			"retry_later": len(retry_later),
			"status": status,
		}

	def export(self, board_id, listed=None):
		"""Both phases for one board (the CLI runs every survey first, then every download)."""
		survey = self.survey(board_id, listed)
		return survey, self.download(survey)
