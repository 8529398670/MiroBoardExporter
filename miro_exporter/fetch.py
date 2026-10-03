"""Pull everything the REST API exposes for one board into an in-memory BoardDump."""

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field

from tqdm import tqdm

from .client import MiroAPIError
from .endpoints import (
	COLLECTIONS,
	DETAIL_LEVEL,
	DOC_CONTENT_TYPES,
	ITEMS,
	ITEMS_BY_TAG_LEVEL,
	board_path,
	doc_content_path,
	experimental_item_path,
	needs_detail,
)
from .util import run_parallel, utc_now_iso

log = logging.getLogger(__name__)

LIST_SOURCE = "list"


@dataclass
class BoardDump:
	board_id: str
	board: dict
	items: dict = field(default_factory=dict)          # item id -> payload (listing merged with detail)
	item_sources: dict = field(default_factory=dict)   # item id -> endpoint the payload came from
	items_total: int | None = None                     # `total` reported by the /items listing
	collections: dict = field(default_factory=dict)    # connectors / groups / tags / members -> list
	docs: dict = field(default_factory=dict)           # doc_format id -> {"markdown": payload, "html": payload}
	item_tags: dict = field(default_factory=dict)      # item id -> [tag id]
	raw_pages: dict = field(default_factory=dict)      # collection name -> [verbatim API pages]
	errors: list = field(default_factory=list)
	_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

	def add_error(self, stage, error, **context):
		entry = {"at": utc_now_iso(), "stage": stage, **context}
		if isinstance(error, MiroAPIError):
			entry.update(url=error.url, status=error.status, body=(error.body or "")[:2000])
		elif error is not None:
			entry.update(error=f"{type(error).__name__}: {error}")
		with self._lock:
			self.errors.append(entry)


class BoardFetcher:
	def __init__(self, client, *, workers=8, details=True, progress=True):
		self.client = client
		self.workers = max(1, workers)
		self.details = details
		self.progress = progress

	def _bar(self, **kwargs):
		return tqdm(leave=False, disable=None if self.progress else True, **kwargs)

	def fetch_board(self, board_id):
		return self.client.get_json(f"/v2/boards/{board_path(board_id)}")

	def run(self, board_id, board=None):
		dump = BoardDump(board_id=board_id, board=board or self.fetch_board(board_id))
		self._fetch_items(dump)
		self._fetch_collections(dump)
		self._fetch_docs(dump)
		if self.details:
			self._fetch_details(dump)
		self._fetch_item_tags(dump)
		return dump

	def _fetch_items(self, dump):
		"""Every item on the board, with no type filter, so new widget types come along automatically."""
		pages = dump.raw_pages.setdefault(ITEMS.name, [])
		bar = self._bar(desc="items", unit="item")

		def on_page(_page_no, page):
			pages.append(page)
			if dump.items_total is None and page.get("total") is not None:
				dump.items_total = page["total"]
				bar.total = page["total"]
				bar.refresh()

		try:
			for item in self.client.paginate_cursor(ITEMS.url(dump.board_id), level=ITEMS.level, on_page=on_page):
				item_id = str(item.get("id"))
				dump.items[item_id] = item
				dump.item_sources[item_id] = LIST_SOURCE
				bar.update(1)
		finally:
			bar.close()
		log.debug("Listed %d items", len(dump.items))

	def _collect(self, coll, board_id, pages, limit=50):
		on_page = lambda _n, page: pages.append(page)
		url = coll.url(board_id)
		if coll.paging == "cursor":
			return list(self.client.paginate_cursor(url, limit=limit, level=coll.level, on_page=on_page))
		return list(self.client.paginate_offset(url, limit=limit or 50, level=coll.level, on_page=on_page))

	def _fetch_collections(self, dump):
		for coll in COLLECTIONS:
			pages = []
			try:
				try:
					rows = self._collect(coll, dump.board_id, pages)
				except MiroAPIError as e:
					# Some experimental endpoints reject an explicit limit; retry with the server default.
					if e.status != 400 or pages or coll.paging != "cursor":
						raise
					rows = self._collect(coll, dump.board_id, pages, limit=None)
			except MiroAPIError as e:
				dump.add_error(f"collection:{coll.name}", e)
				(log.debug if coll.experimental else log.warning)("Could not fetch %s: %s", coll.name, e)
				continue
			finally:
				if pages:
					dump.raw_pages[coll.name] = pages

			if not coll.as_items:
				dump.collections[coll.name] = rows
				continue
			for row in rows:
				item_id = str(row.get("id"))
				merged = {**dump.items.get(item_id, {}), **row}
				merged["type"] = merged.get("type") or coll.default_type
				dump.items[item_id] = merged
				dump.item_sources[item_id] = coll.url(dump.board_id)
			if rows:
				log.debug("Fetched %d %s", len(rows), coll.name)

	def _fetch_docs(self, dump):
		"""Doc format items carry their text only on the per-item endpoint, in markdown or html."""
		doc_ids = [i for i, item in dump.items.items() if item.get("type") == "doc_format"]
		if not doc_ids:
			return

		def fetch(item_id, kind):
			return self.client.get_json(doc_content_path(dump.board_id, item_id), {"textContentType": kind}, DETAIL_LEVEL)

		jobs = [(item_id, kind) for item_id in doc_ids for kind in DOC_CONTENT_TYPES]
		for (item_id, kind), future in run_parallel(fetch, jobs, workers=self.workers, desc="docs", unit="call", progress=self.progress):
			try:
				payload = future.result()
			except MiroAPIError as e:
				dump.add_error("doc_content", e, item_id=item_id, content_type=kind)
				continue
			dump.docs.setdefault(item_id, {})[kind] = payload
			if kind == DOC_CONTENT_TYPES[0]:
				dump.items[item_id] = {**dump.items[item_id], **payload}
				dump.item_sources[item_id] = doc_content_path(dump.board_id, item_id)

	def _fetch_experimental(self, board_id, item_id):
		try:
			return self.client.get_json(experimental_item_path(board_id, item_id), level=DETAIL_LEVEL), None
		except MiroAPIError as e:
			return None, e

	def _fetch_details(self, dump):
		"""Fill in the items the listing couldn't represent (it omits `data` for them).

		Only some types get more from the experimental endpoint (flowchart shapes, mind map nodes);
		others (tables, kanban, paint, ...) get nothing anywhere. So one item per type is probed
		first, and the rest of that type is fetched only if the probe came back with data.
		"""
		pending = defaultdict(list)
		for item_id, item in dump.items.items():
			if dump.item_sources.get(item_id) == LIST_SOURCE and needs_detail(item):
				pending[item.get("type") or "unknown"].append(item_id)
		if not pending:
			return

		def merge(item_id, payload):
			dump.items[item_id] = {**dump.items[item_id], **payload}
			dump.item_sources[item_id] = experimental_item_path(dump.board_id, item_id)

		probes = [(dump.board_id, ids[0]) for ids in pending.values()]
		enrichable = []
		for (_, item_id), future in run_parallel(self._fetch_experimental, probes, workers=self.workers, desc="probe", unit="type", progress=self.progress):
			payload, _error = future.result()
			item_type = dump.items[item_id].get("type") or "unknown"
			if payload is not None and not needs_detail(payload):
				merge(item_id, payload)
				enrichable.append(item_type)
			else:
				log.debug("%d %s items: the listing is all the API has", len(pending[item_type]), item_type)

		jobs = [(dump.board_id, item_id) for item_type in enrichable for item_id in pending[item_type][1:]]
		for (_, item_id), future in run_parallel(self._fetch_experimental, jobs, workers=self.workers, desc="details", unit="item", progress=self.progress):
			payload, error = future.result()
			if payload is None:
				dump.add_error("detail", error, item_id=item_id, item_type=dump.items[item_id].get("type"))
			else:
				merge(item_id, payload)

	def _fetch_item_tags(self, dump):
		for tag in dump.collections.get("tags") or []:
			tag_id = str(tag.get("id"))
			try:
				for item in self.client.paginate_offset(ITEMS.url(dump.board_id), {"tag_id": tag_id}, level=ITEMS_BY_TAG_LEVEL):
					dump.item_tags.setdefault(str(item.get("id")), []).append(tag_id)
			except MiroAPIError as e:
				dump.add_error("item_tags", e, tag_id=tag_id)
