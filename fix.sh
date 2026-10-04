#!/usr/bin/env bash
# One-off: give the files you've already exported their original names, and move every board to
# the one-copy layout (no snapshots/, no assets/ store, no hard links). Delete this script after.
#
#   ./fix.sh                    # everything under ./exports
#   EXPORTS=/some/dir ./fix.sh  # another exports folder
#
# 1. Asks Miro for each stored file's original name. One call per file, no download, so about
#    180 files a minute (Miro's rate limit): ~26,000 images take about 2.5 hours.
# 2. Re-exports those boards with ./miro-export. Their files are moved (renamed, never copied)
#    out of assets/ into the frame folders under their real names; snapshots/ and latest go.
# 3. Rebuilds the viewer site with ./miro-view, so search finds the file names.
#
# Ctrl-C any time and run it again: names already looked up are kept in exports/.fix-names.jsonl.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exports="${EXPORTS:-$here/exports}"
python="$here/.venv/bin/python"
if [ ! -x "$python" ]; then
	echo "Run ./miro-export check once first (it sets up .venv)." >&2
	exit 2
fi

"$python" - "$exports" <<'PY'
import json
import signal
import sys
from pathlib import Path

from miro_exporter.assets import FilePlacer
from miro_exporter.cli import duration
from miro_exporter.client import LEVEL_COST, Cancelled, MiroAPIError, MiroClient
from miro_exporter.config import load_token
from miro_exporter.endpoints import RESOURCE_LEVEL
from miro_exporter.layout import read_json_or
from miro_exporter.util import run_parallel, write_json

NAMED_ROLES = ("image", "document")   # embed previews are named after the embed's title instead

exports = Path(sys.argv[1])
boards_root = exports / "boards"
cache_path = exports / ".fix-names.jsonl"
ids_path = exports / ".fix-boards.txt"
if not boards_root.is_dir():
	sys.exit(f"No boards under {boards_root}")


def read_jsonl(path):
	if not path.is_file():
		return []
	return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def old_plan(board_dir):
	"""The download plan of the board's newest snapshot in the old layout, and the files in its store."""
	snaps = sorted((p for p in (board_dir / "snapshots").glob("*") if (p / "index" / "assets.jsonl").is_file()), reverse=True)
	latest = board_dir / "latest"
	if latest.is_dir() and (latest / "index" / "assets.jsonl").is_file():
		snaps.insert(0, latest)
	if not snaps:
		return [], set()
	stored = set()
	store = board_dir / "assets"
	if store.is_dir():
		for kind in store.iterdir():
			if kind.is_dir():
				stored.update(p.name.split(".", 1)[0] for p in kind.iterdir() if not p.name.startswith("."))
	return read_jsonl(snaps[0] / "index" / "assets.jsonl"), stored


# Which files need a name, per board.
names = {row["key"]: row["name"] for row in read_jsonl(cache_path)}
boards = {}
for board_dir in sorted(p for p in boards_root.iterdir() if p.is_dir() and "__" in p.name):
	if (board_dir / "export.json").is_file():
		files = read_json_or(board_dir / "files.json", {})
		wanted = {key: e["url"] for key, e in files.items() if e.get("path") and not e.get("name") and e.get("role") in NAMED_ROLES and e.get("url")}
	else:
		rows, stored = old_plan(board_dir)
		if not rows and not stored:
			print(f"  {board_dir.name}: nothing exported here, skipped", file=sys.stderr)
			continue
		wanted = {row["key"]: row["url"] for row in rows if row["role"] in NAMED_ROLES and row["key"] in stored}
	boards[board_dir] = wanted

todo = {}
for wanted in boards.values():
	for key, url in wanted.items():
		if key not in names:
			todo.setdefault(key, url)

client = MiroClient(load_token())


def interrupt(_signum, _frame):
	client.cancel.set()  # worker threads stop at their next request
	raise KeyboardInterrupt


signal.signal(signal.SIGINT, interrupt)
placer = FilePlacer(exports, client)


def lookup(key, url):
	try:
		return placer.lookup_name(url), None
	except MiroAPIError as e:
		if e.status in (400, 403, 404, 410):
			return None, None      # Miro refuses this resource: no name to be had
		return None, e             # try again on the next run


print(f"{len(boards)} boards, {sum(len(w) for w in boards.values())} files; {len(names)} names already looked up, "
	f"{len(todo)} to go ({duration(client.bucket.seconds_for(len(todo) * LEVEL_COST[RESOURCE_LEVEL]))}).", file=sys.stderr)
failed = 0
try:
	with open(cache_path, "a", encoding="utf-8") as cache:
		for (key, _url), future in run_parallel(lookup, list(todo.items()), workers=8, desc="names", unit="file"):
			name, error = future.result()
			if error is not None:
				failed += 1
				continue
			names[key] = name
			cache.write(json.dumps({"key": key, "name": name}, ensure_ascii=False) + "\n")
			cache.flush()
except (KeyboardInterrupt, Cancelled):
	print("\nStopped. Run ./fix.sh again to carry on; the names found so far are kept.", file=sys.stderr)
	sys.exit(130)
if failed:
	print(f"{failed} lookups failed (network or server errors); run ./fix.sh again to retry them.", file=sys.stderr)

# Hand the names to the exporter: it keeps a file's name from files.json and renames the file to match.
found = 0
for board_dir, wanted in boards.items():
	if not wanted:
		continue
	files_path = board_dir / "files.json"
	files = read_json_or(files_path, {})
	changed = False
	for key in wanted:
		name = names.get(key)
		if name and (files.get(key) or {}).get("name") != name:
			files.setdefault(key, {})["name"] = name
			changed = True
			found += 1
	if not changed:
		continue
	write_json(files_path, files)
	export = read_json_or(board_dir / "export.json", None)
	if export is not None and export.get("status") == "complete":
		# Already in the new layout: have the next export rename the files.
		export["status"] = "files_pending"
		write_json(board_dir / "export.json", export)
print(f"{found} names to apply.", file=sys.stderr)

ids_path.write_text("".join(f"{board_dir.name.rsplit('__', 1)[1]}\n" for board_dir in boards), encoding="utf-8")
PY

# Re-export those boards: survey them, then move their files into place under the new names.
set +e
"$here/miro-export" --out "$exports" --from-file "$exports/.fix-boards.txt"
status=$?
set -e
if [ "$status" -eq 130 ]; then
	echo "Stopped. Run ./fix.sh again to carry on." >&2
	exit 130
fi

"$here/miro-view" --exports "$exports"

if [ "$status" -ne 0 ]; then
	echo "Some boards failed (see above): they stay in the old layout. Run ./fix.sh again to retry them." >&2
	exit "$status"
fi
rm -f "$exports/.fix-names.jsonl" "$exports/.fix-boards.txt"
echo "Done: every board is in the new layout, and its files carry their original names." >&2
