"""An in-memory fake of the Miro REST API, so the whole exporter runs without network."""

import copy
import json
import re
from urllib.parse import parse_qs, quote, urlencode, urlparse

import pytest
import requests

from miro_exporter.client import CreditBucket, MiroClient

BOARD_ID = "uXjVTEST="
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4 fake document"


def make_response(status=200, body=None, *, content=None, headers=None, url=""):
	resp = requests.Response()
	resp.status_code = status
	resp.headers.update(headers or {})
	if body is not None:
		content = json.dumps(body).encode()
		resp.headers.setdefault("Content-Type", "application/json")
	resp._content = content or b""
	resp._content_consumed = True
	resp.encoding = "utf-8"
	resp.url = url
	return resp


def resource(kind, rid, query):
	return f"https://api.miro.com/v2/boards/{BOARD_ID}/resources/{kind}/{rid}?{query}"


STYLE = {"fillColor": "#ffffff"}


def board_items():
	"""Shaped like real /items listings: styled types carry `style`, and widgets the listing can't
	represent (flowchart shapes, tables, ...) come back without `data`."""
	return [
		{"id": "F1", "type": "frame", "data": {"title": "Intro"}, "style": STYLE,
			"position": {"x": 0, "y": 0, "origin": "center", "relativeTo": "canvas_center"},
			"geometry": {"width": 400, "height": 300}},
		{"id": "F2", "type": "frame", "data": {"title": "Second / Part"}, "style": STYLE,
			"position": {"x": 600, "y": 10, "origin": "center", "relativeTo": "canvas_center"},
			"geometry": {"width": 400, "height": 300}},
		{"id": "F3", "type": "frame", "data": {"title": "<p>Nested</p>"}, "parent": {"id": "F1"}, "style": STYLE,
			"position": {"x": 100, "y": 100, "origin": "center", "relativeTo": "parent_top_left"},
			"geometry": {"width": 100, "height": 100}},
		{"id": "S1", "type": "sticky_note", "data": {"content": "<p>hello</p>"}, "parent": {"id": "F1"}, "style": STYLE,
			"position": {"x": 50, "y": 50, "origin": "center", "relativeTo": "parent_top_left"},
			"geometry": {"width": 20, "height": 20}},
		{"id": "S2", "type": "sticky_note", "data": {"content": "deep"}, "parent": {"id": "F3"}, "style": STYLE,
			"position": {"x": 10, "y": 10, "origin": "center", "relativeTo": "parent_top_left"},
			"geometry": {"width": 10, "height": 10}},
		{"id": "I1", "type": "image", "parent": {"id": "F2"},
			"data": {"imageUrl": resource("images", "9001", "format=preview&redirect=false"), "title": "pic"},
			"position": {"x": 100, "y": 100, "origin": "center", "relativeTo": "parent_top_left"},
			"geometry": {"width": 50, "height": 40}},
		{"id": "I2", "type": "image",
			"data": {"imageUrl": resource("images", "9001", "format=preview&redirect=false")},
			"position": {"x": -900, "y": 0, "origin": "center", "relativeTo": "canvas_center"},
			"geometry": {"width": 50, "height": 40}},
		{"id": "D1", "type": "document",
			"data": {"documentUrl": resource("documents", "9002", "redirect=false"), "title": "notes.pdf"},
			"position": {"x": -900, "y": 300, "origin": "center", "relativeTo": "canvas_center"},
			"geometry": {"width": 100, "height": 140}},
		{"id": "DF1", "type": "doc_format", "parent": {"id": "F2"}, "data": {},
			"position": {"x": 200, "y": 200, "origin": "center", "relativeTo": "parent_top_left"}},
		{"id": "W1", "type": "weird_widget", "data": {"anything": True},
			"position": {"x": 0, "y": 900, "origin": "center", "relativeTo": "canvas_center"}},
		{"id": "SH1", "type": "shape", "data": {"shape": "circle"}, "style": STYLE,
			"position": {"x": 0, "y": 600, "origin": "center", "relativeTo": "canvas_center"},
			"geometry": {"width": 30, "height": 30}},
		{"id": "FS1", "type": "shape", "isSupported": False, "parent": {"id": "F2"},
			"position": {"x": 300, "y": 50, "origin": "center", "relativeTo": "parent_top_left"},
			"geometry": {"width": 40, "height": 20}},
		{"id": "FS2", "type": "shape", "isSupported": False,
			"position": {"x": 0, "y": 700, "origin": "center", "relativeTo": "canvas_center"},
			"geometry": {"width": 40, "height": 20}},
		{"id": "TB1", "type": "table", "isSupported": False,
			"position": {"x": 0, "y": 800, "origin": "center", "relativeTo": "canvas_center"}},
		{"id": "TB2", "type": "table", "isSupported": False,
			"position": {"x": 0, "y": 850, "origin": "center", "relativeTo": "canvas_center"}},
		{"id": "I0", "type": "image",
			"data": {"imageUrl": resource("images", "0", "format=preview&redirect=false")},
			"position": {"x": -900, "y": 600, "origin": "center", "relativeTo": "canvas_center"}},
	]


class FakeMiro:
	"""Mimics requests.Session for both api.miro.com and the signed storage host."""

	page_size = 4

	def __init__(self):
		self.headers = {}
		self.calls = []
		self.items = board_items()
		self.board_modified = "2026-07-30T02:35:17Z"
		self.fail_items = False
		self.refused_resources = set()  # resource ids the API answers 404 for (permanent)
		self.flaky_blobs = set()        # resource ids whose storage download fails with 503 (transient)
		self.blob_downloads = 0
		# What each upload was called. Like Miro, a name gets the stored type's extension appended.
		self.names = {"9001": "Slide1.jpeg.jpg", "9002": "notes.pdf"}

	# requests.Session interface used by the exporter
	def request(self, method, url, params=None, json=None, timeout=None, stream=False):
		return self._handle(method, url, params)

	def get(self, url, stream=False, timeout=None, **_):
		return self._handle("GET", url, None)

	def _handle(self, method, url, params):
		parts = urlparse(url)
		query = {k: v[0] for k, v in parse_qs(parts.query).items()}
		query.update({k: str(v) for k, v in (params or {}).items() if v is not None})
		self.calls.append((method, parts.netloc + parts.path, query))
		full_url = url

		if parts.netloc == "s3.example.com":
			if parts.path.rsplit("/", 1)[-1] in self.flaky_blobs:
				return make_response(503, {"message": "slow down"}, url=full_url)
			self.blob_downloads += 1
			# Storage echoes the disposition the signed link asked for.
			named = {"Content-Disposition": query["response-content-disposition"]} if "response-content-disposition" in query else {}
			if parts.path.endswith("9001"):
				return make_response(content=PNG, headers={"Content-Type": "image/png", "Content-Length": str(len(PNG)), **named}, url=full_url)
			return make_response(content=PDF, headers={"Content-Type": "application/octet-stream", **named}, url=full_url)

		path = parts.path
		board = f"/v2/boards/{BOARD_ID}"
		exp = f"/v2-experimental/boards/{BOARD_ID}"

		if path == board:
			return make_response(body={"id": BOARD_ID, "name": "Test Board", "viewLink": "https://miro.com/app/board/x/", "modifiedAt": self.board_modified})
		if path == f"{board}/items" and "tag_id" in query:
			tagged = [i for i in self.items if i["id"] == "S1"] if query["tag_id"] == "T1" else []
			return self._offset_page(tagged, query)
		if path == f"{board}/items":
			if self.fail_items:
				return make_response(500, {"message": "boom"})
			return self._cursor_page(self.items, query)
		if path == f"{board}/connectors":
			return self._cursor_page([{"id": "C1", "type": "connector", "startItem": {"id": "S1"}, "endItem": {"id": "I1"}, "shape": "curved"}], query)
		if path == f"{board}/groups":
			return self._cursor_page([], query)
		if path == f"{board}/tags":
			return self._offset_page([{"id": "T1", "type": "tag", "title": "important", "fillColor": "red"}], query)
		if path == f"{board}/members":
			return self._offset_page([{"id": "U1", "name": "Someone", "role": "owner", "type": "board_member"}], query)
		if path == f"{exp}/mindmap_nodes":
			return self._cursor_page([{"id": "MM1", "data": {"nodeView": {"type": "text", "data": {"content": "root"}}, "isRoot": True}}], query)
		if path == f"{exp}/code_widgets":
			return make_response(404, {"message": "not found"})

		m = re.fullmatch(rf"{re.escape(board)}/resources/(images|documents)/(\d+)", path)
		if m and m.group(2) == "0":
			return make_response(400, {"code": "2.0703", "message": "Invalid parameters"})
		if m and m.group(2) in self.refused_resources:
			return make_response(404, {"message": "resource not found"})
		if m:
			link = {"sig": "abc"}
			name = self.names.get(m.group(2))
			if name:
				link["response-content-disposition"] = f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name)}"
			return make_response(body={"type": m.group(1), "url": f"https://s3.example.com/blob/{m.group(2)}?{urlencode(link)}"})

		m = re.fullmatch(rf"{re.escape(board)}/docs/(\w+)", path)
		if m:
			kind = query.get("textContentType")
			content = "# Hello" if kind == "markdown" else "<h1>Hello</h1>"
			return make_response(body={"id": m.group(1), "type": "doc_format", "data": {"contentType": kind, "content": content}})

		# Like the real API: fills in flowchart shapes, but has nothing more for tables.
		m = re.fullmatch(rf"{re.escape(exp)}/items/(\w+)", path)
		if m:
			item = next((i for i in self.items if i["id"] == m.group(1)), None)
			if item and item["id"].startswith("FS"):
				return make_response(body={**copy.deepcopy(item), "data": {"shape": "flow_chart_process", "content": "step"}, "style": STYLE})
			if item and item["type"] == "table":
				return make_response(body=copy.deepcopy(item))

		return make_response(404, {"message": f"no route for {path}"})

	def _cursor_page(self, rows, query):
		limit = min(int(query.get("limit") or 10), self.page_size)
		start = int(query.get("cursor") or 0)
		chunk = rows[start:start + limit]
		body = {"data": chunk, "total": len(rows), "size": len(chunk), "limit": limit}
		if start + limit < len(rows):
			body["cursor"] = str(start + limit)
		return make_response(body=body)

	def _offset_page(self, rows, query):
		limit = min(int(query.get("limit") or 10), self.page_size)
		offset = int(query.get("offset") or 0)
		chunk = rows[offset:offset + limit]
		return make_response(body={"data": chunk, "total": len(rows), "size": len(chunk), "offset": offset, "limit": limit})


@pytest.fixture
def fake():
	return FakeMiro()


@pytest.fixture
def client(fake):
	# A huge budget so tests never pace; pacing itself is unit-tested with a fake clock.
	return MiroClient("test-token", session=fake, bucket=CreditBucket(limit=10**12), sleep=lambda s: None)
