"""Allowlist sanitizer for the rich text Miro stores in item content.

Miro writes Quill markup: lists are `<ol>` whose `<li data-list="bullet">` are really bullets,
nesting is `class="ql-indent-N"`, and every list item starts with an empty
`<span class="ql-ui">` that Quill fills in with its own CSS. Colors arrive as inline
`color` / `background-color` styles. Everything else is dropped; text is always kept.
"""

import html
import re
from html.parser import HTMLParser

ALLOWED = {"p", "br", "strong", "b", "em", "i", "u", "s", "strike", "sub", "sup", "code", "span", "a", "ol", "ul", "li"}
VOID = {"br"}
DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "template", "noscript", "title", "head"}
LIST_KINDS = {"bullet", "ordered", "checked", "unchecked"}
BLOCK = {"p", "li", "br", "div", "ol", "ul", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "blockquote"}

_INDENT = re.compile(r"^ql-indent-([1-8])$")
_COLOR = re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\(\s*[\d.]+%?\s*(,\s*[\d.]+%?\s*){2,3}\)|[a-zA-Z]{3,20})$")
_SAFE_HREF = re.compile(r"^(https?:|mailto:)", re.I)


def _style(value):
	kept = []
	for decl in (value or "").split(";"):
		name, _, val = decl.partition(":")
		name, val = name.strip().lower(), val.strip()
		if name in ("color", "background-color") and _COLOR.match(val) and val.lower() not in ("transparent", "inherit", "initial"):
			kept.append(f"{name}:{val}")
	return ";".join(kept)


class _Cleaner(HTMLParser):
	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.out = []
		self.stack = []      # allowed tags currently open
		self.skip = 0        # depth inside dropped-with-content elements
		self.skip_tag = []

	def handle_starttag(self, tag, attrs):
		attrs = dict(attrs)
		classes = (attrs.get("class") or "").split()
		if self.skip or tag in DROP_CONTENT or (tag == "span" and "ql-ui" in classes):
			if tag not in VOID:
				self.skip += 1
				self.skip_tag.append(tag)
			return
		if tag not in ALLOWED:
			return
		kept = []
		style = _style(attrs.get("style"))
		if style:
			kept.append(("style", style))
		if tag == "li":
			kind = attrs.get("data-list")
			if kind in LIST_KINDS:
				kept.append(("data-list", kind))
			indent = next((c for c in classes if _INDENT.match(c)), None)
			if indent:
				kept.append(("class", indent))
		if tag == "a":
			href = (attrs.get("href") or "").strip()
			if not _SAFE_HREF.match(href):
				tag = "span"
			else:
				kept += [("href", href), ("target", "_blank"), ("rel", "noopener noreferrer")]
		text = "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in kept)
		self.out.append(f"<{tag}{text}>")
		if tag not in VOID:
			self.stack.append(tag)

	def handle_startendtag(self, tag, attrs):
		self.handle_starttag(tag, attrs)
		if tag not in VOID and self.stack and self.stack[-1] == tag:
			self.handle_endtag(tag)

	def handle_endtag(self, tag):
		if self.skip:
			if self.skip_tag and self.skip_tag[-1] == tag:
				self.skip_tag.pop()
				self.skip -= 1
			return
		if tag == "a" and "a" not in self.stack and "span" in self.stack:
			tag = "span"   # an <a> we downgraded to a span
		if tag not in self.stack:
			return
		while self.stack:
			open_tag = self.stack.pop()
			self.out.append(f"</{open_tag}>")
			if open_tag == tag:
				break

	def handle_data(self, data):
		if not self.skip:
			self.out.append(html.escape(data, quote=False))

	def result(self):
		while self.stack:
			self.out.append(f"</{self.stack.pop()}>")
		return "".join(self.out)


def sanitize(markup):
	"""Safe HTML for innerHTML. Plain text (no tags) comes back escaped."""
	if not markup:
		return ""
	cleaner = _Cleaner()
	cleaner.feed(str(markup))
	cleaner.close()
	return cleaner.result()


class _Text(HTMLParser):
	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.parts = []
		self.skip = 0

	def handle_starttag(self, tag, attrs):
		classes = (dict(attrs).get("class") or "").split()
		if tag in DROP_CONTENT or (tag == "span" and "ql-ui" in classes) or self.skip:
			if tag not in VOID:
				self.skip += 1
			return
		if tag in BLOCK:
			self.parts.append("\n")

	def handle_endtag(self, tag):
		if self.skip:
			self.skip -= 1
			return
		if tag in BLOCK:
			self.parts.append("\n")

	def handle_data(self, data):
		if not self.skip:
			self.parts.append(data)


def plain_lines(markup):
	"""The text of each paragraph/line, without markup. Used for titles and text-size estimates."""
	if not markup:
		return []
	parser = _Text()
	parser.feed(str(markup))
	parser.close()
	lines = (" ".join(line.split()) for line in "".join(parser.parts).split("\n"))
	return [line for line in lines if line]


def plain_text(markup):
	return " ".join(plain_lines(markup))
