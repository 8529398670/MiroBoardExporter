"""Board geometry and frame hierarchy, computed once and shared by the writer and the index files."""

import math


def parent_id(item):
	parent = (item or {}).get("parent")
	if isinstance(parent, dict) and parent.get("id") is not None:
		return str(parent["id"])
	return None


def _num(value):
	if isinstance(value, bool) or not isinstance(value, (int, float)):
		return None
	return float(value)


def compute_geometry(items):
	"""Absolute canvas geometry for every item.

	Miro positions top-level items (and frames) relative to the canvas center, and children
	relative to their parent's top-left corner, with `origin: center` by default. We resolve
	that chain so the HTML builder can place everything on one coordinate system.
	"""
	top_left_cache = {}
	resolving = set()
	geometry = {}

	def resolve(item_id):
		if item_id in top_left_cache:
			return top_left_cache[item_id]
		item = items.get(item_id)
		if item is None or item_id in resolving:
			return None
		resolving.add(item_id)

		pos = item.get("position") or {}
		geom = item.get("geometry") or {}
		x, y = _num(pos.get("x")), _num(pos.get("y"))
		width, height = _num(geom.get("width")), _num(geom.get("height"))
		top_left = None
		center = None
		if x is not None and y is not None:
			if pos.get("relativeTo") == "parent_top_left":
				pid = parent_id(item)
				offset = resolve(pid) if pid else None
				if offset is not None:
					x, y = x + offset[0], y + offset[1]
				elif pid:
					x = y = None  # parent missing from the export: position can't be made absolute
			if x is not None:
				half_w, half_h = (width or 0) / 2, (height or 0) / 2
				if pos.get("origin", "center") == "center":
					center = (x, y)
					top_left = (x - half_w, y - half_h)
				else:
					top_left = (x, y)
					center = (x + half_w, y + half_h)

		resolving.discard(item_id)
		top_left_cache[item_id] = top_left
		geometry[item_id] = {
			"x": top_left[0] if top_left else None,
			"y": top_left[1] if top_left else None,
			"width": width,
			"height": height,
			"rotation": _num(geom.get("rotation")) or 0.0,
			"center": list(center) if center else None,
		}
		return top_left

	for item_id in items:
		resolve(item_id)
	return geometry


def nearest_frame(items, item_id):
	"""Closest frame among the item's ancestors, or None if it isn't inside a frame."""
	seen = {item_id}
	pid = parent_id(items.get(item_id))
	while pid and pid not in seen:
		seen.add(pid)
		parent = items.get(pid)
		if parent is None:
			return None
		if parent.get("type") == "frame":
			return pid
		pid = parent_id(parent)
	return None


def reading_order(ids, geometry):
	"""Sort boxes top-to-bottom in rows, then left-to-right within a row."""
	placed = [i for i in ids if (geometry.get(i) or {}).get("y") is not None]
	placed_set = set(placed)
	unplaced = sorted(i for i in ids if i not in placed_set)
	placed.sort(key=lambda i: (geometry[i]["y"], geometry[i]["x"] if geometry[i]["x"] is not None else math.inf, i))

	rows = []
	row_bottom = None
	for i in placed:
		g = geometry[i]
		if rows and g["y"] < row_bottom:
			rows[-1].append(i)
		else:
			rows.append([i])
			# A box starting above the middle of the row's first box belongs to the same row.
			row_bottom = g["y"] + (g["height"] or 0) / 2
	ordered = []
	for row in rows:
		ordered.extend(sorted(row, key=lambda i: (geometry[i]["x"] if geometry[i]["x"] is not None else math.inf, i)))
	return ordered + unplaced


def build_frame_tree(items, geometry):
	"""{frame_id: {"parent": frame_id|None, "child_frames": [ordered ids]}} plus ordered root frames."""
	frame_ids = [i for i, item in items.items() if item.get("type") == "frame"]
	tree = {fid: {"parent": nearest_frame(items, fid), "child_frames": []} for fid in frame_ids}
	roots = []
	for fid in frame_ids:
		parent = tree[fid]["parent"]
		(tree[parent]["child_frames"] if parent in tree else roots).append(fid)
	for node in tree.values():
		node["child_frames"] = reading_order(node["child_frames"], geometry)
	return tree, reading_order(roots, geometry)
