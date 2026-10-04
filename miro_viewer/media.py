"""Site-wide media store: image previews and rendered PDF pages. Originals stay in the archive.

	<out>/media/
		img/<key>.<tier>.webp           previews: 256 and 1024 px when the original is larger, and
		                                always a top preview (2048 px at most, else the original's size)
		img/<key>.svg                   SVGs as they are: they draw sharp at any size
		pages/<key>-p<NNN>.<tier>.webp  PDF pages rendered at 2048 px, then downscaled
		index.json                      what was made, so a rebuild only processes new files

The canvas only ever loads these previews: a phone can't decode a few thousand full-size photos,
and it means the site folder alone is enough to browse every board. "Original file" and "Open
PDF" link to the archived files in place (exports/boards/<board>/assets/...), by a path relative
to the site, so they work as long as the archive sits next to the site.

Keys are Miro resource ids. Boards copied from other boards share them, so a file placed on
five boards is processed once. Pillow and PyMuPDF are optional: without Pillow the canvas falls
back to the originals in the archive, without PyMuPDF document pages are drawn as cards.
"""

import logging
import os
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

from tqdm import tqdm

from .util import read_json, write_json

log = logging.getLogger(__name__)

INDEX_VERSION = 1
TIERS = (256, 1024, 2048)
TOP = max(TIERS)
TIER_SLACK = 1.15          # only make a smaller tier when the original is meaningfully larger
MAX_PIXELS = 400_000_000   # Pillow's decompression-bomb guard is far below real scans and posters
WEBP_QUALITY = 80

try:
	import PIL  # noqa: F401
	HAVE_PIL = True
except ImportError:
	HAVE_PIL = False

try:
	import pymupdf  # noqa: F401
	HAVE_PDF = True
except ImportError:
	HAVE_PDF = False


# --- workers (top level so a process pool can pickle them) --------------------

def _average_color(image):
	from PIL import Image
	small = image.copy()
	small.thumbnail((64, 64))
	if small.mode != "RGB":
		background = Image.new("RGB", small.size, (255, 255, 255))
		background.paste(small.convert("RGBA"), mask=small.convert("RGBA").getchannel("A"))
		small = background
	r, g, b = small.resize((1, 1), Image.BOX).getpixel((0, 0))[:3]
	return f"#{r:02x}{g:02x}{b:02x}"


def wanted_tiers(longest):
	"""The smaller tiers the original exceeds, plus the top one (which is never upscaled)."""
	return [t for t in TIERS if t != TOP and longest > t * TIER_SLACK] + [TOP]


def _write_tiers(image, stem, tiers):
	"""Save <stem>.<tier>.webp for each tier, largest first so every step downsizes the last one."""
	from PIL import Image
	current = image
	for tier in sorted(tiers, reverse=True):
		current = current.copy()
		current.thumbnail((tier, tier), Image.LANCZOS)
		current.save(f"{stem}.{tier}.webp", "WEBP", quality=WEBP_QUALITY, method=4)
	return sorted(tiers), current


def make_image_previews(src, stem):
	"""Previews for one image file. Returns {w, h, c, t} (original size, average color, tiers made)."""
	from PIL import Image, ImageFile, ImageOps
	Image.MAX_IMAGE_PIXELS = MAX_PIXELS
	# Browsers show PNGs with a bad checksum on an optional chunk (a color profile, say) and
	# truncated files as far as they go; previews should too.
	ImageFile.LOAD_TRUNCATED_IMAGES = True
	try:
		with Image.open(src) as image:
			w, h = image.size
			if image.getexif().get(0x0112) in (5, 6, 7, 8):
				w, h = h, w
			if image.format == "JPEG":
				image.draft("RGB", (TOP, TOP))
			image.seek(0)
			image = ImageOps.exif_transpose(image)
			has_alpha = image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)
			image = image.convert("RGBA" if has_alpha else "RGB")
			Path(stem).parent.mkdir(parents=True, exist_ok=True)
			tiers, smallest = _write_tiers(image, stem, wanted_tiers(max(w, h)))
			return {"w": w, "h": h, "c": _average_color(smallest), "t": tiers}
	except Exception as exc:   # one unreadable file must not stop a board
		return {"err": f"{type(exc).__name__}: {exc}"}


def render_pdf_pages(src, stem_prefix, pages):
	"""Render the given page indexes of a PDF to tiered previews. Returns {page index: {w, h, c, t}}."""
	import pymupdf
	from PIL import Image
	out = {}
	try:
		with pymupdf.open(src) as doc:
			for n in sorted(set(pages)):
				if not 0 <= n < doc.page_count:
					continue
				page = doc[n]
				zoom = TOP / max(page.rect.width, page.rect.height, 1)
				pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
				image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
				stem = f"{stem_prefix}-p{n + 1:03d}"
				Path(stem).parent.mkdir(parents=True, exist_ok=True)
				tiers, smallest = _write_tiers(image, stem, TIERS)
				out[n] = {"w": pix.width, "h": pix.height, "c": _average_color(smallest), "t": tiers}
	except Exception as exc:
		log.warning("Can't render pages of %s: %s", src, exc)
	return out


# --- the store -------------------------------------------------------------------

class MediaStore:
	def __init__(self, root, *, workers=None, force=False, progress=True):
		self.root = Path(root)
		self.site = self.root.parent
		self.workers = max(1, workers or os.cpu_count() or 2)
		self.force = force
		self.progress = progress
		self.index = {"v": INDEX_VERSION, "img": {}, "page": {}, "doc": {}}
		path = self.root / "index.json"
		if path.is_file() and not force:
			loaded = read_json(path)
			if loaded.get("v") == INDEX_VERSION:
				self.index.update(loaded)
		self._pool = None
		self._keys = {}
		self._done_this_run = set()
		if not HAVE_PIL:
			log.warning("Pillow isn't installed: boards will load full-size originals (pip install Pillow)")
		if not HAVE_PDF:
			log.warning("PyMuPDF isn't installed: document pages are drawn as cards (pip install pymupdf)")

	# -- keys and lookups

	def _key(self, kind, key, path):
		"""The store key for a file. Same resource id but a different file (rare) gets a suffix."""
		memo = (kind, key, str(path))
		if memo in self._keys:
			return self._keys[memo]
		size = path.stat().st_size
		table = self.index["doc" if kind == "doc" else "img"]
		entry = table.get(key)
		store_key = key if entry is None or entry.get("sz") in (None, size) else f"{key}-{size}"
		self._keys[memo] = store_key
		return store_key

	def href(self, path):
		"""A URL for an archived file, relative to the site folder (where the pages are)."""
		return quote(Path(os.path.relpath(path, self.site)).as_posix())

	def page_count(self, key, path):
		if not (HAVE_PDF and Path(path).suffix.lower() == ".pdf"):
			return None
		store_key = self._key("doc", key, path)
		entry = self.index["doc"].get(store_key)
		if entry is None or "pages" not in entry:
			try:
				import pymupdf
				with pymupdf.open(path) as doc:
					pages = doc.page_count
			except Exception as exc:
				log.warning("Can't open %s: %s", path, exc)
				pages = 0
			entry = self.index["doc"][store_key] = {"sz": Path(path).stat().st_size, "pages": pages}
		return entry["pages"] or None

	def can_render(self, key, path):
		return HAVE_PIL and bool(self.page_count(key, path))

	# -- processing

	def _pool_get(self):
		if self._pool is None:
			self._pool = ProcessPoolExecutor(self.workers)
		return self._pool

	def _run(self, jobs, desc):
		"""jobs: [(fn, args, on_done)]. Runs in the process pool with a progress bar."""
		if not jobs:
			return
		pool = self._pool_get()
		futures = {pool.submit(fn, *args): on_done for fn, args, on_done in jobs}
		try:
			bar = tqdm(as_completed(futures), total=len(futures), desc=desc, unit="file", leave=False,
				disable=None if self.progress else True)
			for future in bar:
				futures[future](future.result())
		except BaseException:
			for future in futures:
				future.cancel()
			raise

	def process(self, requests, label=""):
		images, pages = {}, {}
		for req in requests:
			if req.kind == "image":
				images[self._key("image", req.key, req.path)] = req.path
			elif req.kind == "page":
				store_key = self._key("doc", req.key, req.path)
				pages.setdefault(store_key, (req.path, set()))[1].add(req.page)

		jobs = []
		for store_key, path in images.items():
			if path.suffix.lower() == ".svg":
				self._copy_svg(store_key, path)
				continue
			if not HAVE_PIL or store_key in self._done_this_run:
				continue
			entry = self.index["img"].get(store_key)
			if entry and not self.force and self._tiers_exist("img", store_key, entry):
				continue
			self._done_this_run.add(store_key)
			jobs.append((make_image_previews, (str(path), str(self.root / "img" / store_key)),
				self._image_done(store_key, path)))
		if HAVE_PIL:
			for store_key, (path, wanted) in pages.items():
				missing = sorted(n for n in wanted if f"{store_key}#{n}" not in self._done_this_run
					and (self.force or not self._page_ok(store_key, n)))
				if missing:
					self._done_this_run.update(f"{store_key}#{n}" for n in missing)
					jobs.append((render_pdf_pages, (str(path), str(self.root / "pages" / store_key), missing),
						self._pages_done(store_key)))
		self._run(jobs, f"{label} previews".strip())

	def _tiers_exist(self, folder, store_key, entry):
		"""Previews already made. Failures are retried: there are few, and a newer Pillow may read them."""
		tiers = entry.get("t") or ()
		return "err" not in entry and TOP in tiers and all(
			(self.root / folder / f"{store_key}.{t}.webp").is_file() for t in tiers)

	def _page_ok(self, store_key, n):
		entry = self.index["page"].get(f"{store_key}-p{n + 1:03d}")
		return bool(entry) and self._tiers_exist("pages", f"{store_key}-p{n + 1:03d}", entry)

	def _image_done(self, store_key, path):
		def done(result):
			if "err" in result:
				log.warning("Can't make previews of %s: %s", path, result["err"])
			result["sz"] = path.stat().st_size
			self.index["img"][store_key] = result
		return done

	def _pages_done(self, store_key):
		def done(result):
			for n, entry in result.items():
				self.index["page"][f"{store_key}-p{n + 1:03d}"] = entry
		return done

	def _copy_svg(self, store_key, path):
		"""An SVG is its own preview (and usually a few KB): it goes into the store as it is."""
		dest = self.root / "img" / f"{store_key}.svg"
		if dest.is_file() and dest.stat().st_size == path.stat().st_size and not self.force:
			return
		dest.parent.mkdir(parents=True, exist_ok=True)
		shutil.copyfile(path, dest)

	# -- records for data.js

	def resolve(self, req):
		"""What data.js says about a requested file. Media keys (k) are relative to media/, the
		original (o) to the site folder."""
		if req.kind == "doc":
			return self.href(req.original)
		if req.kind == "page":
			store_key = self._key("doc", req.key, req.path)
			name = f"{store_key}-p{req.page + 1:03d}"
			entry = self.index["page"].get(name)
			if not entry:
				return None
			return {"k": f"pages/{name}", "w": entry["w"], "h": entry["h"], "c": entry["c"], "t": entry["t"]}
		store_key = self._key("image", req.key, req.path)
		ext = req.path.suffix.lower()
		record = {"k": f"img/{store_key}", "t": [], "o": self.href(req.original)}
		if ext == ".svg":
			record["v"] = 1
			return record
		entry = self.index["img"].get(store_key) if HAVE_PIL else None
		if entry and "err" not in entry:
			record.update(w=entry["w"], h=entry["h"], c=entry["c"], t=entry["t"])
		if ext == ".gif":
			record["a"] = 1
		return record

	def save(self):
		write_json(self.root / "index.json", self.index, compact=True)

	def close(self):
		if self._pool is not None:
			self._pool.shutdown(wait=True, cancel_futures=True)
			self._pool = None
