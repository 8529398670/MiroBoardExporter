"""Small file helpers, kept local so the viewer depends on the exporter's output, not its code."""

import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path


def read_json(path):
	with open(path, encoding="utf-8") as f:
		return json.load(f)


def read_jsonl(path):
	rows = []
	with open(path, encoding="utf-8") as f:
		for line in f:
			line = line.strip()
			if line:
				rows.append(json.loads(line))
	return rows


def write_text(path, text):
	"""Write atomically (temp file + rename), so an interrupted build never leaves half a page."""
	path = Path(path)
	path.parent.mkdir(parents=True, exist_ok=True)
	tmp = path.with_name(f".{path.name}.tmp")
	with open(tmp, "w", encoding="utf-8") as f:
		f.write(text)
	os.replace(tmp, path)


def write_json(path, obj, compact=False):
	if compact:
		write_text(path, json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
	else:
		write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def script_json(obj):
	"""JSON that is safe inside a <script>: `</script>` and the JS line separators can't end it early."""
	text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
	return text.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text, max_len=48, fallback="board"):
	"""ASCII-only file name part. Board names hold `&`, `,`, `=` and accents, which break file:// links on phones."""
	text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
	text = _NOT_SLUG.sub("-", text).strip("-")[:max_len].strip("-")
	return text or fallback


def short_hash(text, length=8):
	return hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:length]
