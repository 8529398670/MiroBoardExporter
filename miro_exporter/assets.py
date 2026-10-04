"""Binary resources (images, documents, embed previews): one copy of each, inside the board folder.

A file lives in the frame folder of the first item that shows it, named after the upload it
came from: `<original name>__<resource id>.<ext>`. files.json records where each one is, so a
re-export moves a file (a rename, never a copy) when its frame changes, and never downloads a
file it already has.
"""

import logging
import mimetypes
import os
import re
import shutil
from dataclasses import dataclass
from email.message import Message
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, urlparse

import requests

from .client import LEVEL_COST, MiroAPIError, pooled_session
from .endpoints import RESOURCE_LEVEL
from .util import run_parallel, safe_name, set_query, short_hash

log = logging.getLogger(__name__)

# (role in item["_export"]["files"], field in item["data"], kind)
ASSET_FIELDS = (
	("image", "imageUrl", "images"),
	("document", "documentUrl", "documents"),
	("preview", "previewUrl", "previews"),
)
FORMATTED_ROLES = {"image", "preview"}  # roles whose Miro URL takes ?format=original|preview
TITLED_ROLES = {"document", "preview"}  # roles whose item title names the file (documents, embeds)

FRAMES_DIR = "frames"
UNFRAMED_DIR = "_unframed"
LEGACY_STORE = "assets"                 # where the snapshot layout (schema 2) kept every file
LEGACY_LAYOUT = ("snapshots", "latest", "latest.txt")

EXT_OVERRIDES = {
	"image/jpeg": ".jpg",
	"image/jpg": ".jpg",
	"image/pjpeg": ".jpg",
	"image/png": ".png",
	"image/gif": ".gif",
	"image/webp": ".webp",
	"image/svg+xml": ".svg",
	"image/svg": ".svg",
	"image/bmp": ".bmp",
	"image/tiff": ".tiff",
	"application/pdf": ".pdf",
	"text/plain": ".txt",
}
GENERIC_TYPES = {"application/octet-stream", "binary/octet-stream", "application/binary", "application/x-binary"}
# Extensions an upload name can end in. Miro appends its own to some ("Slide1.jpeg" -> "Slide1.jpeg.jpg").
FILE_EXTENSIONS = set(EXT_OVERRIDES.values()) | {
	".jpeg", ".jfif", ".heic", ".heif", ".tif", ".avif", ".ico",
	".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".key", ".pages", ".numbers", ".odt", ".odp", ".ods",
	".csv", ".md", ".rtf", ".zip", ".mp4", ".mov", ".mp3", ".wav", ".bin",
}
# A file this module named: `<stem>__<id>.<ext>`. Anything else in the board folder is left alone.
EXPORTED_NAME = re.compile(r".+__[^/]+\.[A-Za-z0-9]{1,8}")

MAGIC = (
	(b"\x89PNG\r\n\x1a\n", ".png"),
	(b"\xff\xd8\xff", ".jpg"),
	(b"GIF87a", ".gif"),
	(b"GIF89a", ".gif"),
	(b"%PDF", ".pdf"),
	(b"PK\x03\x04", ".zip"),
	(b"<svg", ".svg"),
)


@dataclass(frozen=True)
class AssetRef:
	item_id: str
	role: str
	kind: str
	url: str
	key: str            # Miro resource id (or a hash of a non-Miro URL): one file per key
	title: str | None = None


@dataclass
class AssetResult:
	path: Path | None
	status: str         # downloaded | moved | kept | failed
	error: str | None = None
	bytes: int = 0
	permanent: bool = False  # failed in a way a later run won't fix (the API refused the resource)
	name: str | None = None  # the original upload name, when the download said


def is_miro_api(url):
	return urlparse(url).netloc.endswith("api.miro.com")


def resource_key(url):
	"""Miro resource id from .../resources/images/<id>, else a stable hash of the URL."""
	last = PurePosixPath(urlparse(url).path).name
	if last.isdigit():
		return last
	return short_hash(url)


def collect_asset_refs(items, image_format="original"):
	"""Return (refs to download, missing) where missing lists items that point at no stored file."""
	refs = []
	missing = []
	for item_id, item in items.items():
		data = item.get("data")
		if not isinstance(data, dict):
			continue
		for role, field_name, kind in ASSET_FIELDS:
			url = data.get(field_name)
			if not isinstance(url, str) or not url.startswith("http"):
				continue
			key = resource_key(url)
			if key.strip("0") == "":
				# Resource id 0: the file never finished uploading. Miro rejects the call, so don't spend credits on it.
				missing.append((item_id, role, url))
				continue
			if role in FORMATTED_ROLES and is_miro_api(url):
				url = set_query(url, format=image_format)
				if image_format != "original":
					key = f"{key}-{image_format}"
			title = data.get("title") if role in TITLED_ROLES else None
			refs.append(AssetRef(item_id=item_id, role=role, kind=kind, url=url, key=key, title=title))
	return refs, missing


# --- names --------------------------------------------------------------------

def disposition_filename(value):
	"""The file name in a Content-Disposition value (`filename*=UTF-8''...` or `filename="..."`)."""
	if not value:
		return None
	message = Message()
	message["content-disposition"] = value
	name = message.get_filename()
	name = PurePosixPath(str(name or "").replace("\\", "/")).name.strip()
	return name or None


def link_filename(url):
	"""The file name a signed storage link will download as (its response-content-disposition)."""
	query = dict(parse_qsl(urlparse(url or "").query))
	return disposition_filename(query.get("response-content-disposition"))


def original_name(name):
	"""The name the file was uploaded with: Miro adds its own extension to some ("Slide1.jpeg.jpg")."""
	if not name:
		return None
	path = PurePosixPath(name)
	if len(path.suffixes) >= 2 and path.suffixes[-2].lower() in FILE_EXTENSIONS:
		return path.stem
	return name


def disk_stem(name, fallback):
	"""A file-system-safe stem for a file: its name without the extension (that comes from the file)."""
	path = PurePosixPath(name or "")
	stem = path.stem if path.suffix.lower() in FILE_EXTENSIONS else path.name
	return safe_name(stem, 80, fallback)


def file_name(key, entry, ext):
	"""`<original name>__<resource id><ext>`: readable, and still unique per resource."""
	return f"{disk_stem(entry.get('name') or entry.get('title'), entry.get('role') or 'file')}__{safe_name(key, 100)}{ext}"


def key_of(file_name):
	"""The resource id in a file name this module wrote (or a legacy store name, `<id>.<ext>`)."""
	path = PurePosixPath(file_name)
	stem = file_name[:-len(path.suffix)] if path.suffix else file_name
	return stem.rsplit("__", 1)[1] if "__" in stem else stem


# --- extensions -----------------------------------------------------------------

def sniff_extension(head):
	for magic, ext in MAGIC:
		if head.startswith(magic):
			return ext
	if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
		return ".webp"
	return None


def _clean_suffix(name, max_len=8):
	suffix = PurePosixPath(name or "").suffix.lower()
	if 1 < len(suffix) <= max_len and suffix[1:].isalnum():
		return suffix
	return None


def guess_extension(content_type=None, *, title=None, url=None, head=b""):
	"""Pick a file extension: document title, then Content-Type, then magic bytes, then URL."""
	from_title = _clean_suffix(title)
	if from_title:
		return from_title
	ctype = (content_type or "").split(";")[0].strip().lower()
	if ctype in EXT_OVERRIDES:
		return EXT_OVERRIDES[ctype]
	if ctype and ctype not in GENERIC_TYPES:
		ext = mimetypes.guess_extension(ctype)
		if ext:
			return ext
	sniffed = sniff_extension(head or b"")
	if sniffed:
		return sniffed
	from_url = _clean_suffix(urlparse(url or "").path, max_len=6)
	return from_url or ".bin"


def _posix(path):
	return PurePosixPath(*Path(path).parts).as_posix()


# --- placing files ------------------------------------------------------------------

class FilePlacer:
	"""Puts each entry of a board's files.json where it belongs: moves the files it has, downloads the rest.

	An entry is {kind, role, url, title, items, folder, name, path}; `path` (relative to the board
	folder) is filled in once the file is there.
	"""

	def __init__(self, root, client, *, download_session=None, workers=8, progress=True, timeout=120, attempts=3, sleep=None):
		self.root = Path(root)
		self.client = client
		# Signed storage URLs must be fetched without the Miro Authorization header.
		self.download_session = download_session or pooled_session()
		self.workers = max(1, workers)
		self.progress = progress
		self.timeout = timeout
		self.attempts = attempts
		self.sleep = sleep or client.sleep

	def target(self, key, entry, ext):
		return _posix(Path(entry["folder"]) / file_name(key, entry, ext))

	def locate(self, files):
		"""Point each entry's `path` at its file: the recorded path if the file is there, else a file
		found by its resource id (after a crash between a move and saving files.json, or in the old
		layout's store). Entries with no file get path None."""
		lost = set()
		for key, entry in files.items():
			if not (entry.get("path") and (self.root / entry["path"]).is_file()):
				entry["path"] = None
				lost.add(key)
		if not lost:
			return
		for top in (FRAMES_DIR, UNFRAMED_DIR, LEGACY_STORE):
			for dirpath, _dirs, names in os.walk(self.root / top):
				for name in names:
					key = key_of(name)
					if key in lost and not name.startswith("."):
						files[key]["path"] = _posix(Path(dirpath, name).relative_to(self.root))
						lost.discard(key)

	def unplaced(self, files):
		"""Keys whose file is missing or not yet where (and named how) it belongs."""
		self.locate(files)
		return [
			key for key, entry in files.items()
			if not entry.get("permanent") and (not entry["path"] or entry["path"] != self.target(key, entry, PurePosixPath(entry["path"]).suffix))
		]

	def missing(self, files):
		self.locate(files)
		return [key for key, entry in files.items() if not entry["path"] and not entry.get("permanent")]

	def place(self, files, save=None, save_every=200):
		"""Move or download every file into place. Returns {key: AssetResult}; `save()` is called as
		work completes (and at the end) so an interrupted run keeps what it did."""
		save = save or (lambda: None)
		self.locate(files)
		results = {}
		todo = []
		for key, entry in files.items():
			if not entry["path"]:
				todo.append(key)
				continue
			current = entry["path"]
			wanted = self.target(key, entry, PurePosixPath(current).suffix)
			if current == wanted:
				results[key] = AssetResult(self.root / current, "kept")
				continue
			(self.root / wanted).parent.mkdir(parents=True, exist_ok=True)
			os.replace(self.root / current, self.root / wanted)
			entry["path"] = wanted
			results[key] = AssetResult(self.root / wanted, "moved")
		try:
			if any(r.status == "moved" for r in results.values()):
				save()
			if todo:
				api_calls = sum(1 for key in todo if is_miro_api(files[key]["url"]))
				minutes = self.client.bucket.seconds_for(api_calls * LEVEL_COST[RESOURCE_LEVEL]) / 60
				log.debug("Downloading %d files (about %s at Miro's rate limit), %d already in place",
					len(todo), f"{minutes:.0f} min" if minutes >= 1 else "<1 min", len(results))
			jobs = [(key, files[key]) for key in todo]
			for n, ((key, entry), future) in enumerate(run_parallel(self._download, jobs, workers=self.workers, desc="files", unit="file", progress=self.progress), 1):
				result = future.result()
				results[key] = result
				if result.status == "failed":
					log.warning("File %s failed (items %s): %s", key, ", ".join(i for i, _ in entry["items"]), result.error)
					entry.update(error=result.error, permanent=result.permanent)
				else:
					entry["path"] = _posix(result.path.relative_to(self.root))
					entry["name"] = result.name or entry.get("name")
					entry.pop("error", None)
					entry.pop("permanent", None)
				if n % save_every == 0:
					save()
		finally:
			save()
		return results

	def _open(self, url):
		"""Return a streaming response for the actual file bytes."""
		if not is_miro_api(url):
			resp = self.download_session.get(url, stream=True, timeout=self.timeout)
			resp.raise_for_status()
			return resp
		resp = self.client.request("GET", set_query(url, redirect="false"), level=RESOURCE_LEVEL, stream=True)
		if "json" not in resp.headers.get("Content-Type", ""):
			return resp  # the API sent the file itself
		info = resp.json()
		target = info.get("url") or info.get("downloadUrl") or info.get("link")
		if not target:
			raise ValueError(f"resource response has no download url (keys: {sorted(info)})")
		file_resp = self.download_session.get(target, stream=True, timeout=self.timeout)
		file_resp.raise_for_status()
		return file_resp

	def _download(self, key, entry):
		folder = self.root / entry["folder"]
		folder.mkdir(parents=True, exist_ok=True)
		tmp = folder / f".{safe_name(key, 100)}.part"
		last_error = None
		permanent = False
		for attempt in range(self.attempts):
			try:
				resp = self._open(entry["url"])
				with resp:
					content_type = resp.headers.get("Content-Type")
					expected = 0 if resp.headers.get("Content-Encoding") else int(resp.headers.get("Content-Length") or 0)
					size = 0
					head = b""
					with open(tmp, "wb") as f:
						for chunk in resp.iter_content(1 << 16):
							if not chunk:
								continue
							if len(head) < 16:
								head += chunk[:16 - len(head)]
							f.write(chunk)
							size += len(chunk)
					final_url = resp.url
					name = original_name(disposition_filename(resp.headers.get("Content-Disposition")) or link_filename(final_url))
				if expected and size != expected:
					raise OSError(f"truncated download: got {size} of {expected} bytes")
				title = entry.get("title") if entry.get("role") == "document" else None
				ext = guess_extension(content_type, title=title, url=final_url, head=head)
				final = folder / file_name(key, {**entry, "name": name or entry.get("name")}, ext)
				os.replace(tmp, final)
				return AssetResult(final, "downloaded", bytes=size, name=name)
			except (MiroAPIError, requests.RequestException, OSError, ValueError) as e:
				last_error = e
				tmp.unlink(missing_ok=True)
				if isinstance(e, MiroAPIError) and e.status in (400, 403, 404, 410):
					permanent = True
					break  # the API itself refused this resource; retrying won't help
				if attempt + 1 < self.attempts:
					self.sleep(1 + attempt)
		return AssetResult(None, "failed", error=str(last_error), permanent=permanent)

	def lookup_name(self, url):
		"""The original name of a resource, from the download link Miro hands out, without downloading it."""
		if not is_miro_api(url):
			return None
		with self.client.request("GET", set_query(url, redirect="false"), level=RESOURCE_LEVEL, stream=True) as resp:
			if "json" in resp.headers.get("Content-Type", ""):
				info = resp.json()
				return original_name(link_filename(info.get("url") or info.get("downloadUrl") or info.get("link")))
			return original_name(disposition_filename(resp.headers.get("Content-Disposition")))

	def tidy(self, keep):
		"""Remove what the board no longer has: files this module wrote that `keep` (paths relative to
		the board folder) doesn't list, folders left empty, and the old snapshot layout."""
		keep = set(keep)
		removed = 0
		for top in (FRAMES_DIR, UNFRAMED_DIR):
			for dirpath, _dirs, names in os.walk(self.root / top, topdown=False):
				for name in names:
					rel = _posix(Path(dirpath, name).relative_to(self.root))
					stale_part = name.startswith(".") and name.endswith(".part")
					if stale_part or (not name.startswith(".") and rel not in keep and EXPORTED_NAME.fullmatch(name)):
						os.unlink(Path(dirpath, name))
						removed += 1
				_remove_if_empty(Path(dirpath))
		if removed:
			log.debug("Removed %d files the board no longer has", removed)

		for name in LEGACY_LAYOUT:
			path = self.root / name
			if path.is_symlink() or path.is_file():
				path.unlink()
			elif path.is_dir():
				# Only hard links and JSON: every file it linked to has been moved into place.
				shutil.rmtree(path)
		store = self.root / LEGACY_STORE
		if store.is_dir():
			for dirpath, _dirs, _names in os.walk(store, topdown=False):
				_remove_if_empty(Path(dirpath))
			if store.exists():
				left = sum(len(names) for _, _, names in os.walk(store))
				log.warning("%s: %d files in the old assets/ folder belong to no item on the board; left in place", self.root.name, left)


def _remove_if_empty(folder):
	try:
		names = os.listdir(folder)
	except FileNotFoundError:
		return
	if all(name == ".DS_Store" for name in names):
		for name in names:
			(folder / name).unlink()
		folder.rmdir()
