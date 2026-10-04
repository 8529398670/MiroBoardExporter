"""Which page of its file each document item shows.

Miro puts a PDF or slide deck on the board as one item per page, all pointing at the same
file, and the API doesn't say which page an item is. The layout does: uploading a file makes a
single cover item (page 1), and "show all pages" lays every page out as a grid created within a
second or two. A file can be expanded more than once, and boards copied from other boards keep
the same files. So the items of one file are split into creation batches, each batch is read
in rows, a lone first row above a full row is the cover, and the rest are counted.
"""

from datetime import datetime

BATCH_GAP_SECONDS = 2.5


def _when(item):
	try:
		return datetime.strptime(str(item.get("createdAt"))[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
	except ValueError:
		return None


def _id_key(item):
	text = str(item["id"])
	return (0, int(text), text) if text.isdigit() else (1, 0, text)


def batches(items):
	"""Consecutive (by id) items created within a couple of seconds of each other."""
	out = []
	last = None
	for item in sorted(items, key=_id_key):
		when = _when(item)
		if out and when is not None and last is not None and when - last <= BATCH_GAP_SECONDS:
			out[-1].append(item)
		else:
			out.append([item])
		last = when
	return out


def rows(ids, boxes):
	"""Reading order in rows: a box starting above the middle of a row's first box joins that row."""
	placed = sorted((i for i in ids if i in boxes), key=lambda i: (boxes[i].y, boxes[i].x))
	out, bottom = [], None
	for i in placed:
		box = boxes[i]
		if out and box.y < bottom:
			out[-1].append(i)
		else:
			out.append([i])
			bottom = box.y + box.h / 2
	for row in out:
		row.sort(key=lambda i: boxes[i].x)
	missing = [i for i in ids if i not in boxes]
	if missing:
		out.append(missing)
	return out


def number_pages(docs, boxes, page_count=lambda key: None):
	"""docs: [(item, key)]. Returns {item id: (page index from 0, guessed)}.

	`guessed` is set when the batch and the file disagree on the number of pages.
	"""
	by_key = {}
	for item, key in docs:
		by_key.setdefault(key, []).append(item)
	result = {}
	for key, items in by_key.items():
		count = page_count(key)
		for batch in batches(items):
			ids = [str(i["id"]) for i in batch]
			if len(ids) == 1:
				result[ids[0]] = (0, False)
				continue
			grid = rows(ids, boxes)
			if len(grid) >= 2 and len(grid[0]) == 1 and len(grid[1]) > 1:
				result[grid[0][0]] = (0, False)
				grid = grid[1:]
			pages = [i for row in grid for i in row]
			guessed = bool(count) and len(pages) != count
			for n, item_id in enumerate(pages):
				if count:
					n = n % count if len(pages) > count else min(n, count - 1)
				result[item_id] = (n, guessed)
	return result
