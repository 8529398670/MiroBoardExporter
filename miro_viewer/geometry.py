"""Board geometry the exporter's abs_bbox doesn't settle on its own.

- Auto-height text has no height: the caller estimates one, and abs_bbox.y is its center.
- Mind map children are positioned from their parent node's *center* (the exporter adds the
  parent's top-left), so they are re-derived down each map.
- Frames inside a slide_container all sit on the same box; they are spread into a grid.
- Connectors only name the items they join and a % point on each box. They become absolute
  endpoints plus the outward direction the line leaves in, which shapes the curve.
"""

import math
from dataclasses import dataclass


@dataclass
class Box:
	x: float
	y: float
	w: float
	h: float
	r: float = 0.0   # degrees, about the center

	@property
	def cx(self):
		return self.x + self.w / 2

	@property
	def cy(self):
		return self.y + self.h / 2

	@property
	def sized(self):
		return self.w > 0 and self.h > 0

	def shift(self, dx, dy):
		self.x += dx
		self.y += dy

	def aabb(self):
		"""Axis-aligned bounds of the (possibly rotated) box: (x1, y1, x2, y2)."""
		if not self.r:
			return self.x, self.y, self.x + self.w, self.y + self.h
		a = math.radians(self.r)
		hw = (abs(self.w * math.cos(a)) + abs(self.h * math.sin(a))) / 2
		hh = (abs(self.w * math.sin(a)) + abs(self.h * math.cos(a))) / 2
		return self.cx - hw, self.cy - hh, self.cx + hw, self.cy + hh


def parent_id(item):
	parent = (item or {}).get("parent")
	if isinstance(parent, dict) and parent.get("id") is not None:
		return str(parent["id"])
	return None


def _num(value, default=None):
	try:
		return float(value)
	except (TypeError, ValueError):
		return default


def item_boxes(items, estimate_height):
	"""{id: Box} for every item with a known position. Sizeless items get a zero box at their center."""
	boxes = {}
	for item in items:
		bbox = (item.get("_export") or {}).get("abs_bbox") or {}
		x, y = _num(bbox.get("x")), _num(bbox.get("y"))
		if x is None or y is None:
			continue
		w, h = _num(bbox.get("width")), _num(bbox.get("height"))
		rotation = _num((item.get("_export") or {}).get("rotation"), 0.0) or 0.0
		if w and h is None and item.get("type") == "text":
			h = estimate_height(item, w)
			y -= h / 2   # the exporter could only use the center for an axis with no size
		boxes[str(item["id"])] = Box(x, y, w or 0.0, h or 0.0, rotation)
	return boxes


def recenter_mindmaps(by_id, boxes):
	"""Place mind map nodes from their parent node's center, the way Miro positions them."""
	done = {}

	def center(node_id):
		if node_id in done:
			return done[node_id]
		done[node_id] = None   # cycle guard
		item, box = by_id.get(node_id), boxes.get(node_id)
		if box is None:
			return None
		pid = parent_id(item)
		pos = item.get("position") or {}
		if pid and (by_id.get(pid) or {}).get("type") == "mindmap_node" and pos.get("x") is not None:
			parent_center = center(pid)
			if parent_center:
				box.x = parent_center[0] + _num(pos.get("x"), 0.0) - box.w / 2
				box.y = parent_center[1] + _num(pos.get("y"), 0.0) - box.h / 2
		done[node_id] = (box.cx, box.cy)
		return done[node_id]

	for node_id, item in by_id.items():
		if item.get("type") == "mindmap_node":
			center(node_id)


def children_map(items):
	kids = {}
	for item in items:
		pid = parent_id(item)
		if pid:
			kids.setdefault(pid, []).append(str(item["id"]))
	return kids


def shift_tree(root_id, dx, dy, boxes, kids):
	stack, seen = [root_id], set()
	while stack:
		node = stack.pop()
		if node in seen:
			continue
		seen.add(node)
		if node in boxes:
			boxes[node].shift(dx, dy)
		stack.extend(kids.get(node, ()))


def layout_slides(items, boxes, frame_order):
	"""Spread the frames of each slide_container (all stacked on one box) into a grid, contents and all.

	Returns {container id: [frame ids]} for the containers that were laid out.
	"""
	by_id = {str(i["id"]): i for i in items}
	containers = {fid for fid, item in by_id.items() if item.get("type") == "slide_container"}
	if not containers:
		return {}
	order = {fid: n for n, fid in enumerate(frame_order)}
	slides = {}
	for fid, item in by_id.items():
		pid = parent_id(item)
		if item.get("type") == "frame" and pid in containers and fid in boxes:
			slides.setdefault(pid, []).append(fid)
	kids = children_map(items)
	laid_out = {}
	for container, frames in slides.items():
		if len(frames) < 2:
			continue
		frames.sort(key=lambda f: (order.get(f, math.inf), int(f) if f.isdigit() else f))
		cell_w = max(boxes[f].w for f in frames) or 1.0
		cell_h = max(boxes[f].h for f in frames) or 1.0
		gap = 0.08 * max(cell_w, cell_h)
		cols = max(1, min(len(frames), math.ceil(math.sqrt(len(frames) * cell_h / cell_w))))
		x0, y0 = boxes[frames[0]].x, boxes[frames[0]].y
		for n, fid in enumerate(frames):
			row, col = divmod(n, cols)
			dx = x0 + col * (cell_w + gap) - boxes[fid].x
			dy = y0 + row * (cell_h + gap) - boxes[fid].y
			shift_tree(fid, dx, dy, boxes, kids)
		laid_out[container] = frames
	return laid_out


def _rotate(x, y, degrees):
	if not degrees:
		return x, y
	a = math.radians(degrees)
	c, s = math.cos(a), math.sin(a)
	return x * c - y * s, x * s + y * c


def _percent(value):
	if value is None:
		return None
	text = str(value).strip()
	scale = 100.0 if text.endswith("%") else 1.0
	number = _num(text.rstrip("%"))
	return None if number is None else number / scale


def anchor(box, position, toward):
	"""(x, y, nx, ny): where a line meets `box`, and the unit direction it leaves the box in.

	`position` is Miro's {"x": "100%", "y": "50%"} on the unrotated box. Without one, the line
	runs between the two centers and meets the box where it crosses the edge. A point inside the
	box leaves through its nearest side.
	"""
	cx, cy = box.cx, box.cy
	if not box.sized:
		dx, dy = toward[0] - cx, toward[1] - cy
		length = math.hypot(dx, dy) or 1.0
		return cx, cy, dx / length, dy / length
	hw, hh = box.w / 2, box.h / 2
	fx = _percent((position or {}).get("x"))
	fy = _percent((position or {}).get("y"))
	if fx is None or fy is None:
		lx, ly = _rotate(toward[0] - cx, toward[1] - cy, -box.r)
		if not lx and not ly:
			lx = 1.0
		tx = hw / abs(lx) if lx else math.inf
		ty = hh / abs(ly) if ly else math.inf
		t = min(tx, ty)
		px, py = lx * t, ly * t
		nx, ny = (math.copysign(1, lx), 0.0) if tx <= ty else (0.0, math.copysign(1, ly))
	else:
		px, py = (fx - 0.5) * box.w, (fy - 0.5) * box.h
		sides = [(fx * box.w, -1.0, 0.0), ((1 - fx) * box.w, 1.0, 0.0), (fy * box.h, 0.0, -1.0), ((1 - fy) * box.h, 0.0, 1.0)]
		_, nx, ny = min(sides, key=lambda s: s[0])
	px, py = _rotate(px, py, box.r)
	nx, ny = _rotate(nx, ny, box.r)
	return cx + px, cy + py, nx, ny


def mindmap_edge(parent, child, vertical=False):
	"""Anchors for the branch from a parent node to a child: out of the facing sides."""
	if vertical:
		sign = 1.0 if child.cy >= parent.cy else -1.0
		return (parent.cx, parent.cy + sign * parent.h / 2, 0.0, sign), (child.cx, child.cy - sign * child.h / 2, 0.0, -sign)
	sign = 1.0 if child.cx >= parent.cx else -1.0
	return (parent.cx + sign * parent.w / 2, parent.cy, sign, 0.0), (child.cx - sign * child.w / 2, child.cy, -sign, 0.0)


def union(rects):
	"""Bounds of (x1, y1, x2, y2) rects, or None."""
	rects = list(rects)
	if not rects:
		return None
	return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))
