# Miro Exporter

Backs up Miro boards through the standard v2 REST API (any plan, no Enterprise export needed).
It saves every item of every type with its data and style, plus connectors, tags, groups,
members, mind map nodes, code widgets, doc content, and the original image/document files.

A run works in two phases:

1. **Survey.** Every board's metadata is saved first: items, frames, connectors, indexes,
   and a list of the files to download. Boards unchanged since their last snapshot are skipped
   without any API calls. This takes minutes even for dozens of boards, so the structure and
   text of everything is backed up before the slow part starts. It ends with a plan, e.g.
   `412 files to download for 9 boards (about 3 min)`.
2. **Files.** Images, documents and embed previews are downloaded board by board into a
   per-board store shared by all snapshots, then linked into the snapshot. When a board's
   files are done, its snapshot is `complete` and becomes `latest`.

## Setup

```bash
cp config.example.toml config.toml   # then paste your token, or export MIRO_ACCESS_TOKEN
./miro-export check                  # first run creates .venv and installs dependencies
```

The token needs the `boards:read` scope ([create one](https://developers.miro.com/docs/rest-api-build-your-first-hello-world-app)).

## Usage

```bash
./miro-export                         # back up every board the token can see
./miro-export --no-assets             # survey only: save all metadata now, files on a later run
./miro-export asdfbase64=            # specific boards (ids or full miro.com URLs)
./miro-export --from-file boards.txt  # ids, URLs, "name === id" lines, or a boards.json
./miro-export --force                 # new snapshots even for unchanged boards
./miro-export list                    # prints "name === id", writes exports/boards.json
./miro-export check                   # who the token belongs to + its scopes
```

`./miro-export` is shorthand for `./miro-export export`. Other useful flags: `--out DIR`,
`--workers N` (requests in parallel within a board), `--survey-workers N` (boards surveyed at
once), `--asset-format preview`, `-v`.

**Crashed or pressed Ctrl-C?** Run the same command again.
- The survey skips every board whose metadata is already saved and unchanged.
- Downloading continues where it stopped; files already stored are never fetched twice.
- Unfinished snapshots are removed once a newer snapshot of that board completes.

## Speed

Miro gives each token 100,000 API credits per minute. Costs, measured on real boards:

| Call | Credits | Max per minute |
|---|---|---|
| list 50 items | 100 | ~1,000 pages (50,000 items) |
| download one image/document | 500 | ~200 files |
| board info, docs, item fill-in | 50 | ~2,000 calls |

- **The survey** is limited by round trips, not credits, so several boards are surveyed at once.
  About 33,000 items across 74 boards take ~3 minutes; unchanged boards take seconds.
- **File downloads** are limited by credits. The client paces at 90% of the budget, about
  180 files a minute, so it never trips Miro's 60-second penalty for going over. Adding
  workers won't make them faster.

## Output

```
exports/boards/<Board Name>__<board_id>/
  assets/{images,documents,previews}/<resource_id>.<ext>   # shared by all snapshots
  latest -> snapshots/<newest complete snapshot>
  snapshots/<YYYY-MM-DDTHHMMSSZ>/
    manifest.json        # status, board modifiedAt, counts per type, asset + API stats, schema_version
    board.json
    frames/01 <title>__<id>/frame.json
    frames/01 <title>__<id>/<type>/<id>.json                # + <id>.png / .pdf / .md / .html
    frames/01 <title>__<id>/01 <nested frame>__<id>/...
    _unframed/<type>/<id>.json
    connectors.json  tags.json  groups.json  members.json
    index/items.jsonl    # one row per item: type, parent, frame, paths, absolute bbox, tags
    index/frame_tree.json
    index/item_tags.json
    index/assets.jsonl   # the download plan: which file goes next to which item
    raw/<collection>/page-NNNN.json                        # verbatim API pages
    errors.jsonl         # non-fatal gaps (e.g. an image whose file never finished uploading)
```

- `manifest.json` `status` is one of:
  - `assets_pending`: metadata saved, files still downloading
  - `complete`
  - `interrupted` or `failed`

  `latest` only ever points at a `complete` snapshot.
- Item JSON is the API payload unchanged, plus an `_export` key with its paths, absolute
  canvas bbox/center, assets, docs, tags and `source` (which endpoint it came from).
  `_export.assets` fills in once the files are downloaded.
- Item types are never hardcoded: a type the exporter has never seen gets its own folder.
- The `/items` listing already includes data and style for every supported type. For items
  it can't represent (it omits their `data`), the exporter tries Miro's experimental endpoint
  on one item per type. That fills in flowchart shapes and mind map nodes. For types where
  nothing more exists (tables, kanban, paint, ...) it keeps the listing data and makes no
  further calls.
- Frames are numbered in reading order (rows top-to-bottom, then left-to-right).
- A file Miro refuses outright (a deleted resource, say) is logged in `errors.jsonl` and doesn't
  hold the snapshot back. Network or server errors leave the snapshot `assets_pending`, so the
  next run retries just those files.
- Board comments aren't available through the REST API.

## Tests

```bash
.venv/bin/pytest
```

The tests run the whole exporter against an in-memory fake of the API, with no network.
