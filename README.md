# Miro Exporter

Backs up Miro boards through the standard v2 REST API (any plan, no Enterprise export needed).
It saves every item of every type with its data and style, plus connectors, tags, groups,
members, mind map nodes, code widgets, doc content, and the original image/document files.

A run works in two phases:

1. **Survey.** Every board's metadata is saved first: items, frames, connectors, and a list
   of the files to download. Boards unchanged since their last export are skipped without any
   API calls. This takes minutes even for dozens of boards, so the structure and
   text of everything is backed up before the slow part starts. It ends with a plan, e.g.
   `412 files to download for 9 boards (about 3 min)`.
2. **Files.** Images, documents and embed previews are downloaded board by board, straight
   into the folder of the frame they sit in, named after the file that was uploaded. Each file
   is stored once: no copies, no links. A file already on disk is moved (renamed) if its frame
   changed, never downloaded again. When a board's files are done, it is `complete`.

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
./miro-export --force                 # re-export even unchanged boards
./miro-export list                    # prints "name === id", writes exports/boards.json
./miro-export check                   # who the token belongs to + its scopes
```

`./miro-export` is shorthand for `./miro-export export`. Other useful flags: `--out DIR`,
`--workers N` (requests in parallel within a board), `--survey-workers N` (boards surveyed at
once), `--asset-format preview`, `-v`.

**Crashed or pressed Ctrl-C?** Run the same command again.
- The survey skips every board whose metadata is already saved and unchanged.
- Downloading continues where it stopped; files already stored are never fetched twice.
- A failed survey leaves the board's saved folder as it was.

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

Each board is one folder, updated in place on every export (there is no snapshot history):

```
exports/boards/<Board Name>__<board_id>/
  export.json      # status, board modifiedAt, counts per type, file + API stats, errors, schema_version
  board.json
  items.json       # every item: the API payload + `_export`
  files.json       # every file: where it is, the name it was uploaded with, which items show it
  frames.json      # the frame tree, in reading order
  connectors.json  tags.json  groups.json  members.json
  frames/01 <title>__<id>/<original name>__<resource_id>.<ext>   # each file once, e.g. Slide1__3458….jpg
  frames/01 <title>__<id>/<doc title>__<item_id>.md / .html      # doc format items
  frames/01 <title>__<id>/01 <nested frame>__<id>/...
  _unframed/...
```

- `export.json` `status` is `files_pending` (metadata saved, files still to download or move
  into place) or `complete`. A failed survey adds a `last_error` and changes nothing else.
- `items.json` keeps each API payload unchanged, plus an `_export` key with its frame, folder,
  absolute canvas bbox/center, files (`role -> resource id` in `files.json`), docs, tags and
  `source` (which endpoint it came from).
- Files are named after the upload Miro recorded (it appends its own extension to some, e.g.
  `Slide1.jpeg.jpg`; that is dropped). Pasted images were never named, so they come out as
  `image__<resource_id>.png`. A file shown by several items sits in the first item's frame.
- An item deleted in Miro has its file removed on the next export. Files you add to a board
  folder yourself are left alone.
- Item types are never hardcoded: a type the exporter has never seen is kept in `items.json` like any other.
- The `/items` listing already includes data and style for every supported type. For items
  it can't represent (it omits their `data`), the exporter tries Miro's experimental endpoint
  on one item per type. That fills in flowchart shapes and mind map nodes. For types where
  nothing more exists (tables, kanban, paint, ...) it keeps the listing data and makes no
  further calls.
- Frames are numbered in reading order (rows top-to-bottom, then left-to-right).
- A file Miro refuses outright (a deleted resource, say) is logged in `export.json` `errors` and
  doesn't hold the board back. Network or server errors leave it `files_pending`, so the next
  run retries just those files.
- Board comments aren't available through the REST API.

## Viewer

`./miro-view` turns the archived boards into a pan-and-zoom HTML site, built for phones first.
It is a separate package (`miro_viewer/`). It only reads what the exporter wrote; it never
imports the exporter's code.

```bash
./miro-view                       # every board -> exports/site/index.html (first run installs Pillow + PyMuPDF)
./miro-view MCAT AC               # just these boards (ids, URLs or parts of names), added to the site
./miro-view --out ~/Desktop/site  # somewhere else
```

The site only holds previews; it never copies the originals. "Original file" and "Open PDF"
link to the archived files where they are (`exports/boards/<board>/frames/...`), by a path
relative to the site.

The site works from `file://` with no server, so it can be copied to a phone.
- Copying the `site/` folder alone is enough to browse every board (about 2 GB). The links to
  originals only work when the archive comes along, i.e. when the whole `exports/` folder is
  copied.
- On an iPhone, open it with an app that serves local HTML. The Files app's Quick Look doesn't
  run JavaScript.
- To try it over the network, run `python3 -m http.server -d exports` and open
  `/site/index.html`.

```
exports/site/
  index.html  boards.js                  # the board list
  <board-slug>.html                      # one page per board
  static/css/  static/js/                # shared by every page (plain scripts, no build step)
  boards/<board-slug>/data.js            # the board's items, links and frames
  media/img/<key>.{256,1024,2048}.webp   # previews: smaller tiers only below the original's size,
                                         # the top one (2048 px at most) always
  media/pages/<key>-p<N>.<tier>.webp     # rendered PDF pages
```

- **Pan, pinch and keys.** Drag or flick to pan; pinch, scroll or double-tap to zoom.
  Keys: `←`/`→` frames, `0` fit, `+`/`-` zoom, `/` search.
- **Frames and search.** The dock steps through frames in reading order. Search covers
  every item's text and title, including the file name an image was uploaded with.
- **Tapping an item** shows its text, who added it and when. From there you can view the
  picture larger, open the document, open the link, or open the item in Miro. The address
  (`#<item id>`) links to that item, and each board reopens where you left it.
- **Pictures are drawn at the size they appear.** A picture shows as its average color when
  tiny, then as a 256, 1024 or 2048 px preview, within a decode budget. That keeps a
  7,000-image board usable on a phone. Previews are shared across boards, so a file used on
  several boards is processed once. Rebuilds only process new files. GIFs show their first
  frame on the board and play in "View larger".
- **Document pages.** Each page of a PDF or slide deck is its own item on the board. The page
  number is worked out from the upload layout: a cover item, then a grid of pages. PDF pages
  are rendered; PPT/PPTX pages appear as cards that open the file.
- **Things Miro's API can't describe** (tables, kanban boards) appear as labelled placeholders.
  Pen drawings have no shape in the archive and are counted as skipped. Kanban cards, which
  come without a position, are listed in the Frames panel.
- **Stacked slide frames** (a presentation's frames all share one position) are laid out as a
  grid.

## Tests

```bash
.venv/bin/pytest
```

The tests run the whole exporter against an in-memory fake of the API, with no network. The
viewer's tests build sites from board folders written in the exporter's layout. If `node` is
installed, they also run the browser code's unit tests in `tests/js/`.
