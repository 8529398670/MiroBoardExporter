import hashlib
import html
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from tqdm import tqdm


def write_json(path, obj):
	"""Write JSON atomically (temp file + rename) so an interrupted run never leaves half a file."""
	path = Path(path)
	path.parent.mkdir(parents=True, exist_ok=True)
	tmp = path.with_name(f".{path.name}.tmp")
	with open(tmp, "w", encoding="utf-8") as f:
		json.dump(obj, f, ensure_ascii=False, indent=2)
		f.write("\n")
	os.replace(tmp, path)


def write_text(path, text):
	path = Path(path)
	path.parent.mkdir(parents=True, exist_ok=True)
	tmp = path.with_name(f".{path.name}.tmp")
	with open(tmp, "w", encoding="utf-8") as f:
		f.write(text)
	os.replace(tmp, path)


def write_jsonl(path, rows):
	path = Path(path)
	path.parent.mkdir(parents=True, exist_ok=True)
	tmp = path.with_name(f".{path.name}.tmp")
	with open(tmp, "w", encoding="utf-8") as f:
		for row in rows:
			f.write(json.dumps(row, ensure_ascii=False))
			f.write("\n")
	os.replace(tmp, path)


def read_json(path):
	with open(path, encoding="utf-8") as f:
		return json.load(f)


_TAGS = re.compile(r"<[^>]+>")
_UNSAFE = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]+')
_SPACES = re.compile(r"\s+")


def safe_name(text, max_len=60, fallback="untitled"):
	"""Turn a Miro title (which may contain HTML) into a filesystem-safe folder/file name."""
	text = html.unescape(_TAGS.sub(" ", str(text or "")))
	text = _UNSAFE.sub(" ", text)
	text = _SPACES.sub(" ", text).strip().strip(".")
	text = text[:max_len].strip()
	return text or fallback


_BOARD_URL = re.compile(r"/app/(?:board|live-embed|embed)/([^/?#]+)")


def parse_board_id(value):
	"""Accept a raw board id or any miro.com board URL and return the board id."""
	value = value.strip()
	match = _BOARD_URL.search(value)
	if match:
		return unquote(match.group(1))
	return unquote(value.strip("/"))


def set_query(url, **params):
	"""Return url with the given query params replaced/added."""
	parts = urlparse(url)
	query = dict(parse_qsl(parts.query, keep_blank_values=True))
	query.update({k: v for k, v in params.items() if v is not None})
	return urlunparse(parts._replace(query=urlencode(query)))


def short_hash(text, length=16):
	return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


def utc_now_iso():
	return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_stamp():
	"""Timestamp used for snapshot folder names (no colons, sorts chronologically)."""
	return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def run_parallel(fn, jobs, *, workers, desc, unit, progress=True):
	"""Yield (job, future) as each call of fn(*job) finishes, with a progress bar.

	Unlike a plain `with ThreadPoolExecutor()`, Ctrl-C cancels the queued work
	instead of waiting for every remaining request to run.
	"""
	pool = ThreadPoolExecutor(max(1, workers))
	try:
		futures = {pool.submit(fn, *job): job for job in jobs}
		bar = tqdm(as_completed(futures), total=len(futures), desc=desc, unit=unit, leave=False, disable=None if progress else True)
		for future in bar:
			yield futures[future], future
	finally:
		pool.shutdown(wait=True, cancel_futures=True)
