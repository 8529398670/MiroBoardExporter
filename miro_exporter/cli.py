import argparse
import logging
import signal
import sys
from pathlib import Path

from tqdm import tqdm

from .client import LEVEL_COST, Cancelled, MiroAPIError, MiroClient
from .config import ConfigError, load_token
from .endpoints import RESOURCE_LEVEL
from .exporter import PENDING, BoardExporter
from .util import parse_board_id, read_json, run_parallel, write_json

log = logging.getLogger("miro_exporter")


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
	logging.getLogger("urllib3").setLevel(logging.WARNING)


def read_board_file(path):
	"""Board ids from a text file (ids, URLs, or `name === id` lines) or a boards.json from `list`."""
	path = Path(path)
	if path.suffix == ".json":
		return [str(b["id"]) for b in read_json(path) if isinstance(b, dict) and b.get("id")]
	ids = []
	for line in path.read_text(encoding="utf-8").splitlines():
		line = line.split("#", 1)[0].strip()
		if not line:
			continue
		if "===" in line:
			line = line.rsplit("===", 1)[1].strip()
		ids.append(parse_board_id(line))
	return ids


def list_boards(client, **filters):
	params = {k: v for k, v in filters.items() if v}
	return list(client.paginate_offset("/v2/boards", params))


def cmd_check(client, args):
	info = client.get_json("/v1/oauth-token")
	user = info.get("user") or {}
	team = info.get("team") or {}
	org = info.get("organization") or {}
	scopes = info.get("scopes") or []
	print(f"User:   {user.get('name')} ({user.get('id')})")
	print(f"Team:   {team.get('name')} ({team.get('id')})")
	if org:
		print(f"Org:    {org.get('name')} ({org.get('id')})")
	print(f"Scopes: {', '.join(scopes) or '(none reported)'}")
	if scopes and "boards:read" not in scopes:
		print("This token is missing the boards:read scope, which every export needs.", file=sys.stderr)
		return 1
	return 0


def cmd_list(client, args):
	boards = list_boards(client, query=args.query, team_id=args.team_id, project_id=args.project_id)
	out = Path(args.out) / "boards.json"
	write_json(out, boards)
	for board in boards:
		print(f"{board.get('name')} === {board.get('id')}")
	print(f"\n{len(boards)} boards -> {out}", file=sys.stderr)
	return 0


INTERRUPTED = ("Interrupted. Run it again to pick up where it stopped: unchanged boards are skipped, "
	"saved metadata is reused, and downloaded files are kept.")


def resolve_targets(client, args):
	"""[(board_id, entry from the boards listing or None)], in order, without duplicates."""
	targets = {}
	for value in args.boards:
		targets.setdefault(parse_board_id(value), None)
	for path in args.from_file or []:
		for board_id in read_board_file(path):
			targets.setdefault(board_id, None)
	if args.all or not (args.boards or args.from_file):
		# The listing carries every board's modifiedAt: unchanged boards then cost no calls at all.
		for board in list_boards(client):
			targets[str(board["id"])] = board
	return list(targets.items())


def say(message):
	"""Print a progress line without breaking an active progress bar."""
	tqdm.write(message, file=sys.stderr)


def plural(n, word):
	return f"{n} {word}" if n == 1 else f"{n} {word}s"


def describe(survey):
	if survey.reused and survey.status == PENDING:
		return f"{survey.name}: unchanged, {survey.to_download} of {plural(survey.planned_files, 'file')} still to download"
	if survey.reused:
		return f"{survey.name}: unchanged"
	files = f"{plural(survey.planned_files, 'file')} ({survey.to_download} new)" if survey.planned_files else "no files"
	return f"{survey.name}: saved {plural(survey.items, 'item')}, {files}"


def duration(seconds):
	minutes = seconds / 60
	if minutes < 1:
		return "under a minute"
	if minutes < 90:
		return f"about {minutes:.0f} min"
	return f"about {minutes / 60:.1f} h"


def cmd_export(client, args):
	targets = resolve_targets(client, args)
	if not targets:
		print("No boards to export.", file=sys.stderr)
		return 0

	exporter = BoardExporter(
		client,
		args.out,
		workers=args.workers,
		details=not args.no_details,
		asset_format=args.asset_format,
		force=args.force,
		progress=not args.no_progress,
	)
	failures = []

	# Phase 1: every board's metadata (items, frames, connectors, indexes) before any slow downloads.
	# Surveys are mostly waiting on round trips rather than rate limit, so several boards run at once.
	say(f"Surveying {plural(len(targets), 'board')}...")
	surveys = {}
	several = len(targets) > 1
	jobs = [(i, board_id, listed) for i, (board_id, listed) in enumerate(targets)]

	def survey_one(_index, board_id, listed):
		return exporter.survey(board_id, listed, progress=False if several else None)

	try:
		boards = run_parallel(survey_one, jobs, workers=args.survey_workers, desc="boards", unit="board", progress=several and not args.no_progress)
		for done, ((index, board_id, _), future) in enumerate(boards, 1):
			try:
				surveys[index] = future.result()
			except (MiroAPIError, OSError, ValueError) as e:
				failures.append(board_id)
				log.error("[%d/%d] board %s failed: %s", done, len(targets), board_id, e)
				log.debug("Traceback", exc_info=True)
				continue
			say(f"  [{done}/{len(targets)}] {describe(surveys[index])}")
	except (KeyboardInterrupt, Cancelled):
		say(INTERRUPTED)
		return 130
	surveys = [surveys[i] for i in sorted(surveys)]

	pending = [s for s in surveys if s.status == PENDING]
	new = sum(1 for s in surveys if not s.reused)
	to_download = sum(s.to_download for s in pending)
	eta = duration(client.bucket.seconds_for(to_download * LEVEL_COST[RESOURCE_LEVEL]))
	say(f"Survey done: {new} updated, {len(surveys) - new} unchanged, {len(failures)} failed. "
		f"{plural(to_download, 'file')} to download for {plural(len(pending), 'board')} ({eta}).")

	# Phase 2: files, board by board: moved into place if already on disk, downloaded otherwise.
	complete = len(surveys) - len(pending)
	still_pending = 0
	if pending and args.no_assets:
		say("Skipping downloads (--no-assets); the next run picks them up without re-fetching metadata.")
	for n, survey in enumerate(pending, 1):
		if args.no_assets:
			still_pending += 1
			continue
		say(f"  [{n}/{len(pending)}] {survey.name}: {plural(survey.to_download, 'file')} to download, {survey.planned_files - survey.to_download} on disk")
		try:
			result = exporter.download(survey)
		except (KeyboardInterrupt, Cancelled):
			say(INTERRUPTED)
			return 130
		except (MiroAPIError, OSError, ValueError) as e:
			failures.append(survey.board_id)
			log.error("Downloading files for %s failed: %s", survey.name, e)
			log.debug("Traceback", exc_info=True)
			continue
		if result is None or result["status"] != PENDING:
			complete += 1
		else:
			still_pending += 1
		if result:
			retry = f", {result['retry_later']} to retry next run" if result["retry_later"] else ""
			say(f"      {result['downloaded']} downloaded, {result['moved']} moved or renamed, {result['kept']} already in place, {result['failed']} failed{retry}")

	say(f"Done: {complete} boards complete, {still_pending} still pending, {len(failures)} failed")
	if failures:
		print(f"Failed boards: {', '.join(failures)}", file=sys.stderr)
		return 1
	return 0


def build_parser():
	common = argparse.ArgumentParser(add_help=False)
	common.add_argument("--token", help="Miro access token (default: $MIRO_ACCESS_TOKEN or config.toml)")
	common.add_argument("--config", help="config file with [miro] access_token (default: ./config.toml, then the project's)")
	common.add_argument("--out", default="exports", help="output folder (default: %(default)s)")
	common.add_argument("-v", "--verbose", action="store_true")

	parser = argparse.ArgumentParser(
		prog="miro-export",
		description="Back up Miro boards via the REST API. With no command, exports every board the token can see.",
	)
	sub = parser.add_subparsers(dest="command", required=True)

	check = sub.add_parser("check", parents=[common], help="show who the token belongs to and its scopes")
	check.set_defaults(func=cmd_check)

	lister = sub.add_parser("list", parents=[common], help="list boards the token can see and write <out>/boards.json")
	lister.add_argument("--query", help="search boards by name")
	lister.add_argument("--team-id")
	lister.add_argument("--project-id")
	lister.set_defaults(func=cmd_list)

	export = sub.add_parser("export", parents=[common], help="export boards, each into its own folder updated in place (the default command)")
	export.add_argument("boards", nargs="*", help="board ids or miro.com board URLs (default: every board the token can see)")
	export.add_argument("--from-file", action="append", metavar="FILE", help="file of board ids/URLs/`name === id` lines, or a boards.json")
	export.add_argument("--all", action="store_true", help="also export every board the token can see")
	export.add_argument("--force", action="store_true", help="re-export boards even if they haven't changed since their last export")
	export.add_argument("--workers", type=int, default=8, help="parallel requests within a board (default: %(default)s)")
	export.add_argument("--survey-workers", type=int, default=4, help="boards surveyed at once (default: %(default)s)")
	export.add_argument("--no-details", action="store_true", help="skip filling in items the listing can't represent (flowchart shapes, mind map nodes)")
	export.add_argument("--no-assets", action="store_true", help="survey only: save every board's metadata now, download files on a later run")
	export.add_argument("--asset-format", choices=("original", "preview"), default="original", help="image resolution to download (default: %(default)s)")
	export.add_argument("--no-progress", action="store_true", help="hide progress bars")
	export.set_defaults(func=cmd_export)
	return parser


COMMANDS = ("check", "list", "export")


def default_to_export(argv):
	"""`miro-export` alone (or with only export options/board ids) means `miro-export export`."""
	if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help")):
		return ["export", *argv]
	return argv


def main(argv=None):
	argv = default_to_export(list(sys.argv[1:] if argv is None else argv))
	args = build_parser().parse_args(argv)
	setup_logging(args.verbose)
	try:
		token = load_token(args.token, args.config)
	except ConfigError as e:
		print(e, file=sys.stderr)
		return 2
	client = MiroClient(token)

	def interrupt(_signum, _frame):
		client.cancel.set()  # worker threads stop at their next request
		raise KeyboardInterrupt

	signal.signal(signal.SIGINT, interrupt)
	try:
		return args.func(client, args)
	except MiroAPIError as e:
		print(f"Miro API error: {e}", file=sys.stderr)
		return 1
	except KeyboardInterrupt:
		return 130
