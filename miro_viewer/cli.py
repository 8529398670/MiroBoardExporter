"""Command line: `miro-view build [boards...]` turns archived boards into the viewer site."""

import argparse
import logging
import sys
import time
from pathlib import Path
from urllib.parse import unquote

from tqdm import tqdm

from .site import build_site
from .source import find_boards

log = logging.getLogger("miro_viewer")


class TqdmLogHandler(logging.Handler):
	"""Log through tqdm so messages don't break progress bars."""

	def emit(self, record):
		try:
			tqdm.write(self.format(record), file=sys.stderr)
		except Exception:
			self.handleError(record)


def setup_logging(verbose):
	handler = TqdmLogHandler()
	handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
	root = logging.getLogger()
	root.handlers[:] = [handler]
	root.setLevel(logging.DEBUG if verbose else logging.INFO)
	logging.getLogger("PIL").setLevel(logging.WARNING)


def say(message):
	tqdm.write(message, file=sys.stderr)


def select_boards(board_dirs, wanted):
	"""Board folders matching any of `wanted`: a board id, a miro.com URL, a folder name or part of a name."""
	if not wanted:
		return list(board_dirs), []
	chosen, unknown = [], []
	for value in wanted:
		value = value.strip()
		if "/app/board/" in value:
			value = unquote(value.split("/app/board/", 1)[1].split("/")[0].split("?")[0])
		matches = [d for d in board_dirs if d.name == value or d.name.rsplit("__", 1)[-1] == value]
		if not matches:
			needle = value.lower()
			matches = [d for d in board_dirs if needle in d.name.rsplit("__", 1)[0].lower()]
		if not matches:
			unknown.append(value)
		chosen.extend(d for d in matches if d not in chosen)
	return chosen, unknown


def build_parser():
	parser = argparse.ArgumentParser(prog="miro-view", description="Build the pan-and-zoom HTML viewer for archived Miro boards.")
	sub = parser.add_subparsers(dest="command")
	build = sub.add_parser("build", help="build or update the viewer site (the default command)")
	build.add_argument("boards", nargs="*", help="board ids, URLs, folder names or parts of names (default: every board)")
	build.add_argument("--exports", default="exports", help="the exporter's output folder (default: exports)")
	build.add_argument("--out", help="site folder (default: <exports>/site)")
	build.add_argument("--workers", type=int, help="processes making previews (default: one per CPU)")
	build.add_argument("--force", action="store_true", help="remake previews and page renders that already exist")
	build.add_argument("--no-progress", action="store_true", help="no progress bars")
	build.add_argument("-v", "--verbose", action="store_true")
	return parser


def main(argv=None):
	argv = list(sys.argv[1:] if argv is None else argv)
	if not argv or argv[0] not in ("build", "-h", "--help"):
		argv.insert(0, "build")
	args = build_parser().parse_args(argv)
	setup_logging(args.verbose)

	exports = Path(args.exports)
	available = find_boards(exports)
	if not available:
		log.error("No archived boards under %s (run ./miro-export first, or pass --exports)", exports / "boards")
		return 1
	boards, unknown = select_boards(available, args.boards)
	for value in unknown:
		log.error("No archived board matches %r", value)
	if unknown or not boards:
		return 1

	out = Path(args.out) if args.out else exports / "site"
	started = time.monotonic()
	bar = tqdm(total=len(boards), desc="Boards", unit="board", disable=True if args.no_progress else None)

	def on_board(summary):
		bar.update(1)
		skipped = sum(summary["skipped"].values())
		extra = f", {skipped} not drawable" if skipped else ""
		extra += f", {summary['unplaced']} without a position" if summary["unplaced"] else ""
		say(f"{summary['name']}: {summary['items']} items, {summary['frames']} frames{extra}")

	try:
		build_site(boards, out, workers=args.workers, force=args.force, progress=not args.no_progress, on_board=on_board)
	except KeyboardInterrupt:
		bar.close()
		say("Stopped. Run the same command again to continue; finished previews are kept.")
		return 130
	bar.close()
	say(f"Built {len(boards)} board{'s' if len(boards) != 1 else ''} in {time.monotonic() - started:.0f}s. Open {out / 'index.html'}")
	return 0
