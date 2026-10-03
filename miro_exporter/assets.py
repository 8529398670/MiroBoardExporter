"""Binary resources (images, documents, embed previews) in a per-board store shared by all snapshots.

Files are keyed by Miro resource id, so a resource is downloaded once and every later
snapshot just hardlinks it next to the item JSON.
"""

import logging
import mimetypes
import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import requests

from .client import LEVEL_COST, MiroAPIError, pooled_session
from .endpoints import RESOURCE_LEVEL
from .util import run_parallel, set_query, short_hash

log = logging.getLogger(__name__)

# (role in item["_export"]["assets"], field in item["data"], store folder)
ASSET_FIELDS = (
	("image", "imageUrl", "images"),
	("document", "documentUrl", "documents"),
	("preview", "previewUrl", "previews"),
)
FORMATTED_ROLES = {"image", "preview"}  # roles whose Miro URL takes ?format=original|preview

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
	key: str            # file stem in the store
	title: str | None = None

	@classmethod
	def from_plan(cls, row):
		"""Rebuild a ref from a row of a snapshot's index/assets.jsonl."""
		return cls(item_id=row["item_id"], role=row["role"], kind=row["kind"], url=row["url"], key=row["key"], title=row.get("title"))


@dataclass
class AssetResult:
	path: Path | None
	status: str         # downloaded | reused | failed
	error: str | None = None
	bytes: int = 0
	permanent: bool = False  # failed in a way a later run won't fix (the API refused the resource)


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
			title = data.get("title") if role == "document" else None
			refs.append(AssetRef(item_id=item_id, role=role, kind=kind, url=url, key=key, title=title))
	return refs, missing


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


def link_or_copy(src, dst):
	"""Hardlink src to dst (no extra disk space); copy if the filesystem can't link."""
	dst = Path(dst)
	dst.parent.mkdir(parents=True, exist_ok=True)
	if dst.exists() or dst.is_symlink():
		dst.unlink()
	try:
		os.link(src, dst)
	except OSError:
		shutil.copy2(src, dst)


class AssetStore:
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
		self._stored = {}  # kind -> {key: path}, read from disk once per kind

	def _index(self, kind):
		if kind not in self._stored:
			found = {}
			folder = self.root / kind
			if folder.is_dir():
				for entry in os.scandir(folder):
					if entry.is_file() and not entry.name.startswith("."):
						found[entry.name.split(".", 1)[0]] = Path(entry.path)
			self._stored[kind] = found
		return self._stored[kind]

	def find(self, kind, key):
		return self._index(kind).get(key)

	def count_missing(self, refs):
		"""How many distinct files these refs need that aren't in the store yet."""
		return len({(ref.kind, ref.key) for ref in refs if self.find(ref.kind, ref.key) is None})

	def fetch_all(self, refs):
		"""Return {(item_id, role): AssetResult}; each distinct resource is fetched at most once."""
		groups = {}
		for ref in refs:
			groups.setdefault((ref.kind, ref.key), []).append(ref)

		results = {}
		todo = []
		for (kind, key), group in groups.items():
			existing = self.find(kind, key)
			if existing:
				for ref in group:
					results[(ref.item_id, ref.role)] = AssetResult(existing, "reused")
			else:
				todo.append(group)

		if todo:
			api_calls = sum(1 for group in todo if is_miro_api(group[0].url))
			minutes = self.client.bucket.seconds_for(api_calls * LEVEL_COST[RESOURCE_LEVEL]) / 60
			log.debug("Downloading %d files (about %s at Miro's rate limit), %d already stored",
				len(todo), f"{minutes:.0f} min" if minutes >= 1 else "<1 min", len(groups) - len(todo))

		jobs = [(group[0],) for group in todo]
		groups_by_ref = {group[0]: group for group in todo}
		for (ref,), future in run_parallel(self._download, jobs, workers=self.workers, desc="assets", unit="file", progress=self.progress):
			result = future.result()
			if result.status == "failed":
				log.warning("Asset failed for item %s (%s): %s", ref.item_id, ref.role, result.error)
			else:
				self._index(ref.kind)[ref.key] = result.path
			results[(ref.item_id, ref.role)] = result
			# Other items showing the same resource reuse the file just fetched.
			shared = AssetResult(result.path, "reused" if result.status == "downloaded" else result.status, result.error, permanent=result.permanent)
			for other in groups_by_ref[ref][1:]:
				results[(other.item_id, other.role)] = shared
		return results

	def _open(self, ref):
		"""Return a streaming response for the actual file bytes."""
		if not is_miro_api(ref.url):
			resp = self.download_session.get(ref.url, stream=True, timeout=self.timeout)
			resp.raise_for_status()
			return resp
		resp = self.client.request("GET", set_query(ref.url, redirect="false"), level=RESOURCE_LEVEL, stream=True)
		if "json" not in resp.headers.get("Content-Type", ""):
			return resp  # the API sent the file itself
		info = resp.json()
		target = info.get("url") or info.get("downloadUrl") or info.get("link")
		if not target:
			raise ValueError(f"resource response has no download url (keys: {sorted(info)})")
		file_resp = self.download_session.get(target, stream=True, timeout=self.timeout)
		file_resp.raise_for_status()
		return file_resp

	def _download(self, ref):
		folder = self.root / ref.kind
		folder.mkdir(parents=True, exist_ok=True)
		tmp = folder / f".{ref.key}.part"
		last_error = None
		permanent = False
		for attempt in range(self.attempts):
			try:
				resp = self._open(ref)
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
				if expected and size != expected:
					raise OSError(f"truncated download: got {size} of {expected} bytes")
				ext = guess_extension(content_type, title=ref.title, url=final_url, head=head)
				final = folder / f"{ref.key}{ext}"
				os.replace(tmp, final)
				return AssetResult(final, "downloaded", bytes=size)
			except (MiroAPIError, requests.RequestException, OSError, ValueError) as e:
				last_error = e
				tmp.unlink(missing_ok=True)
				if isinstance(e, MiroAPIError) and e.status in (400, 403, 404, 410):
					permanent = True
					break  # the API itself refused this resource; retrying won't help
				if attempt + 1 < self.attempts:
					self.sleep(1 + attempt)
		return AssetResult(None, "failed", error=str(last_error), permanent=permanent)
