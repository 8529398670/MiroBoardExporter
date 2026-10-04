from email.utils import formatdate

import pytest

from miro_exporter.assets import (
	FilePlacer,
	collect_asset_refs,
	disk_stem,
	disposition_filename,
	guess_extension,
	key_of,
	link_filename,
	original_name,
	resource_key,
)
from miro_exporter.cli import default_to_export, read_board_file
from miro_exporter.client import Cancelled, CreditBucket, MiroAPIError, MiroClient
from miro_exporter.index import compute_geometry, nearest_frame, reading_order
from miro_exporter.util import parse_board_id, safe_name

from .conftest import BOARD_ID, PNG, make_response


# --- util -------------------------------------------------------------------

def test_safe_name_strips_html_and_path_characters():
	assert safe_name("<p>A/B: c?</p>") == "A B c"
	assert safe_name("", fallback="Frame") == "Frame"
	assert safe_name("  ...  ", fallback="x") == "x"
	assert len(safe_name("x" * 200, 60)) == 60
	assert safe_name("Tom &amp; Jerry") == "Tom & Jerry"


@pytest.mark.parametrize("value", [
	"uXjVPeMngYY=",
	"uXjVPeMngYY%3D",
	"https://miro.com/app/board/uXjVPeMngYY=/",
	"https://miro.com/app/board/uXjVPeMngYY=/?share_link_id=123",
	"https://miro.com/app/live-embed/uXjVPeMngYY=/?moveToViewport=1",
])
def test_parse_board_id(value):
	assert parse_board_id(value) == "uXjVPeMngYY="


def test_read_board_file_accepts_ids_urls_and_old_list_format(tmp_path):
	path = tmp_path / "boards.txt"
	path.write_text(
		"# my boards\n"
		"Biochemistry === uXjVPeMngYY=\n"
		"\n"
		"https://miro.com/app/board/uXjVMHzVTPE=/\n"
		"o9J_lHD7LhI=  # old one\n"
	)
	assert read_board_file(path) == ["uXjVPeMngYY=", "uXjVMHzVTPE=", "o9J_lHD7LhI="]


# --- client -----------------------------------------------------------------

class ScriptedSession:
	def __init__(self, responses):
		self.headers = {}
		self.responses = list(responses)
		self.params = []

	def request(self, method, url, params=None, json=None, timeout=None, stream=False):
		self.params.append(dict(params or {}))
		return self.responses.pop(0)


def test_cursor_pagination_follows_cursor_and_stops():
	session = ScriptedSession([
		make_response(body={"data": [1, 2], "cursor": "c1"}),
		make_response(body={"data": [3], "cursor": "c2"}),
		make_response(body={"data": [4]}),
	])
	client = MiroClient("t", session=session, sleep=lambda s: None)
	assert list(client.paginate_cursor("/x")) == [1, 2, 3, 4]
	assert [p.get("cursor") for p in session.params] == [None, "c1", "c2"]


def test_cursor_pagination_guards_against_repeated_cursor():
	session = ScriptedSession([
		make_response(body={"data": [1], "cursor": "same"}),
		make_response(body={"data": [2], "cursor": "same"}),
	])
	client = MiroClient("t", session=session, sleep=lambda s: None)
	assert list(client.paginate_cursor("/x")) == [1, 2]


def test_offset_pagination_stops_at_total():
	session = ScriptedSession([
		make_response(body={"data": [1, 2], "total": 3}),
		make_response(body={"data": [3], "total": 3}),
	])
	client = MiroClient("t", session=session, sleep=lambda s: None)
	assert list(client.paginate_offset("/x", limit=2)) == [1, 2, 3]
	assert [p["offset"] for p in session.params] == [0, 2]


def test_client_retries_rate_limit_and_server_errors():
	now = [0.0]
	sleeps = []

	def sleep(seconds):
		sleeps.append(seconds)
		now[0] += seconds

	session = ScriptedSession([
		make_response(429, {"message": "slow down"}, headers={"Retry-After": "3"}),
		make_response(503, {"message": "busy"}),
		make_response(body={"ok": True}),
	])
	bucket = CreditBucket(clock=lambda: now[0], sleep=sleep)
	client = MiroClient("t", session=session, bucket=bucket, sleep=sleep)
	assert client.get_json("/x") == {"ok": True}
	stats = client.stats_snapshot()
	assert stats["rate_limit_waits"] == 1 and stats["retries"] == 1 and stats["calls"] == 3
	assert sum(sleeps) >= 3  # waited out the Retry-After (in short naps) before calling again


def test_client_raises_on_client_errors():
	client = MiroClient("t", session=ScriptedSession([make_response(404, {"message": "nope"})]), sleep=lambda s: None)
	with pytest.raises(MiroAPIError) as info:
		client.get_json("/x")
	assert info.value.status == 404


def fake_clock():
	now = [0.0]
	slept = []

	def sleep(seconds):
		slept.append(seconds)
		now[0] += seconds

	return now, slept, sleep


def test_credit_bucket_paces_at_a_steady_rate_under_the_budget():
	now, slept, sleep = fake_clock()
	# 60k/min at 100% = 1000 credits/s, with a 2s (2000 credit) burst allowance.
	bucket = CreditBucket(limit=60_000, fraction=1.0, burst_seconds=2, clock=lambda: now[0], sleep=sleep)
	bucket.take(1000)
	bucket.take(1000)
	assert slept == []
	bucket.take(500)
	assert slept == [pytest.approx(0.5)]


def test_credit_bucket_pause_is_shared_and_reported_once():
	now, slept, sleep = fake_clock()
	bucket = CreditBucket(clock=lambda: now[0], sleep=sleep)
	assert bucket.block_for(10) is True
	assert bucket.block_for(9) is False   # another thread seeing the same reset: no second log line
	bucket.take(50)
	assert sum(slept) >= 10


def test_reset_wait_uses_the_server_clock():
	server_now = 1_791_049_572
	resp = make_response(body={}, headers={"X-RateLimit-Reset": str(server_now + 10), "Date": formatdate(server_now, usegmt=True)})
	assert MiroClient._seconds_until_reset(resp) == 11  # 10s to the reset + 1s margin, whatever the local clock says


def test_resource_downloads_are_charged_as_level_3_and_named_after_the_upload(client, fake, tmp_path):
	url = f"https://api.miro.com/v2/boards/{BOARD_ID}/resources/images/9001?format=original"
	placer = FilePlacer(tmp_path, client, download_session=fake, progress=False)
	files = {"9001": {"kind": "images", "role": "image", "url": url, "title": None, "items": [["I1", "image"]], "folder": "_unframed", "name": None, "path": None}}
	results = placer.place(files)
	assert results["9001"].status == "downloaded"
	assert (files["9001"]["path"], files["9001"]["name"]) == ("_unframed/Slide1__9001.png", "Slide1.jpeg")
	assert (tmp_path / "_unframed" / "Slide1__9001.png").read_bytes() == PNG
	assert client.stats_snapshot()["credits"] == 500


def test_lookup_name_reads_the_download_link_without_downloading(client, fake, tmp_path):
	url = f"https://api.miro.com/v2/boards/{BOARD_ID}/resources/images/9001?format=original"
	placer = FilePlacer(tmp_path, client, download_session=fake, progress=False)
	assert placer.lookup_name(url) == "Slide1.jpeg"
	assert fake.blob_downloads == 0 and client.stats_snapshot()["credits"] == 500


@pytest.mark.parametrize("value, expected", [
	("attachment; filename=\"image.png\"; filename*=UTF-8''image.png", "image.png"),
	("attachment; filename*=UTF-8''Caf%C3%A9%20menu.pdf", "Café menu.pdf"),
	('attachment; filename="Slide1.jpeg.jpg"', "Slide1.jpeg.jpg"),
	('attachment; filename="../../etc/x.png"', "x.png"),
	("attachment", None),
	(None, None),
])
def test_disposition_filename(value, expected):
	assert disposition_filename(value) == expected


def test_link_filename_reads_the_signed_links_disposition():
	link = "https://r.miro.com/1/original.png?response-content-disposition=attachment%3B%20filename%3D%22Slide1.jpeg.jpg%22&Signature=x"
	assert link_filename(link) == "Slide1.jpeg.jpg"
	assert link_filename("https://r.miro.com/1/preview?Signature=x") is None


@pytest.mark.parametrize("name, expected", [
	("Slide1.jpeg.jpg", "Slide1.jpeg"),   # Miro appends the stored type's extension
	("photo.png.png", "photo.png"),
	("v1.2.png", "v1.2.png"),
	("notes.pdf", "notes.pdf"),
	(None, None),
])
def test_original_name_drops_the_extension_miro_appends(name, expected):
	assert original_name(name) == expected


@pytest.mark.parametrize("name, expected", [
	("Slide1.jpeg", "Slide1"),
	("Lecture 3: intro.PDF", "Lecture 3 intro"),
	("v1.2", "v1.2"),
	("", "image"),
	(None, "image"),
])
def test_disk_stem(name, expected):
	assert disk_stem(name, "image") == expected


@pytest.mark.parametrize("name, expected", [
	("Slide1__9001.png", "9001"),
	("a__b__9001.jpg", "9001"),
	("9001.png", "9001"),               # the old store's naming
	("9001-preview.png", "9001-preview"),
])
def test_key_of(name, expected):
	assert key_of(name) == expected


# --- geometry ---------------------------------------------------------------

ITEMS = {
	"F": {"id": "F", "type": "frame", "position": {"x": 0, "y": 0, "origin": "center", "relativeTo": "canvas_center"}, "geometry": {"width": 200, "height": 100}},
	"G": {"id": "G", "type": "group_like", "parent": {"id": "F"}, "position": {"x": 10, "y": 20, "origin": "center", "relativeTo": "parent_top_left"}, "geometry": {"width": 10, "height": 10}},
	"S": {"id": "S", "type": "sticky_note", "parent": {"id": "G"}, "position": {"x": 1, "y": 1, "origin": "center", "relativeTo": "parent_top_left"}, "geometry": {"width": 2, "height": 2}},
	"T": {"id": "T", "type": "text", "position": {"x": 5, "y": 5, "origin": "center", "relativeTo": "canvas_center"}, "geometry": {"width": 10}},
	"O": {"id": "O", "type": "text", "parent": {"id": "missing"}, "position": {"x": 5, "y": 5, "relativeTo": "parent_top_left"}},
}


def test_geometry_resolves_parent_chain_to_absolute_canvas_coordinates():
	geo = compute_geometry(ITEMS)
	assert (geo["F"]["x"], geo["F"]["y"]) == (-100, -50)
	assert geo["G"]["center"] == [-90, -30]          # frame top-left + (10, 20)
	assert (geo["S"]["x"], geo["S"]["y"]) == (-95, -35)  # G top-left (-95,-35) + (1,1) - half size
	assert geo["T"]["height"] is None and geo["T"]["center"] == [5, 5]
	assert geo["O"]["x"] is None                     # parent not exported: no fake absolute position


def test_nearest_frame_walks_through_non_frame_parents():
	assert nearest_frame(ITEMS, "S") == "F"
	assert nearest_frame(ITEMS, "T") is None
	assert nearest_frame(ITEMS, "O") is None


def test_reading_order_groups_rows_then_sorts_left_to_right():
	geo = {
		"a": {"x": 500, "y": 5, "height": 100},
		"b": {"x": 0, "y": 0, "height": 100},
		"c": {"x": 0, "y": 300, "height": 100},
		"d": {"x": None, "y": None, "height": None},
	}
	assert reading_order(["a", "b", "c", "d"], geo) == ["b", "a", "c", "d"]


# --- assets -----------------------------------------------------------------

@pytest.mark.parametrize("kwargs, expected", [
	({"content_type": "image/jpeg"}, ".jpg"),
	({"content_type": "image/svg+xml; charset=utf-8"}, ".svg"),
	({"content_type": "application/octet-stream", "title": "Lecture 3.PDF"}, ".pdf"),
	({"content_type": "application/octet-stream", "head": PNG[:16]}, ".png"),
	({"content_type": "application/octet-stream", "url": "https://s3/x/file.docx?sig=1"}, ".docx"),
	({"content_type": None}, ".bin"),
])
def test_guess_extension(kwargs, expected):
	assert guess_extension(**kwargs) == expected


def test_collect_asset_refs_requests_original_images():
	items = {
		"1": {"type": "image", "data": {"imageUrl": "https://api.miro.com/v2/boards/b/resources/images/42?format=preview&redirect=false"}},
		"2": {"type": "embed", "data": {"previewUrl": "https://cdn.example.com/thumb.jpg"}},
		"3": {"type": "sticky_note", "data": {"content": "x"}},
		"4": {"type": "image", "data": {"imageUrl": "https://api.miro.com/v2/boards/b/resources/images/0?format=preview"}},
	}
	refs, missing = collect_asset_refs(items)
	refs = {r.item_id: r for r in refs}
	assert set(refs) == {"1", "2"}
	assert [m[0] for m in missing] == ["4"]
	assert "format=original" in refs["1"].url and refs["1"].key == "42"
	assert refs["2"].role == "preview" and refs["2"].url == "https://cdn.example.com/thumb.jpg"
	assert resource_key("https://cdn.example.com/thumb.jpg") == refs["2"].key


@pytest.mark.parametrize("argv, expected", [
	([], ["export"]),
	(["--force"], ["export", "--force"]),
	(["uXjVPeMngYY="], ["export", "uXjVPeMngYY="]),
	(["list"], ["list"]),
	(["export", "--all"], ["export", "--all"]),
	(["-h"], ["-h"]),
])
def test_bare_command_means_export(argv, expected):
	assert default_to_export(argv) == expected


def test_cancel_stops_requests_and_rate_limit_pauses():
	now, slept, sleep = fake_clock()
	bucket = CreditBucket(clock=lambda: now[0], sleep=sleep)
	client = MiroClient("t", session=ScriptedSession([]), bucket=bucket, sleep=sleep)
	bucket.block_for(60)

	def cancel_after_a_few_naps(seconds):
		sleep(seconds)
		if len(slept) == 3:
			client.cancel.set()

	bucket.sleep = cancel_after_a_few_naps
	with pytest.raises(Cancelled):
		client.get_json("/x")
	assert sum(slept) < 5  # gave up within seconds, not after the full 60s pause
