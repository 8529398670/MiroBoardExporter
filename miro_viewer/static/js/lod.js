/* Level of detail for pictures: which file to show for an image at its size on screen, and a
   decode budget so a phone never holds more pixels than it can keep in memory.

   A media record (built by media.py) has k: its key in media/, t: the preview tiers that exist
   (256/1024/2048 px on the long side), w/h: the original's size, c: average color, v: SVG,
   a: animated GIF, o: the original in the archive (relative to the site). Pure functions, no DOM. */
(function (MV) {
	'use strict';

	var L = MV.lod = {
		COLOR_BOX_PX: 40,   // below this many screen pixels a picture is just its average color
		TOP: 2048,
		KEEP_SMALLER: 0.75, // keep the current source until it is this much too small...
		KEEP_LARGER: 3      // ...or this much larger than needed
	};

	/* The files a record offers, smallest first: [{id, px}] where px is the long side. The top
	   tier is never upscaled, so it may be smaller than its name. Originals stay out of the
	   canvas unless no preview could be made. */
	L.sources = function (m) {
		var list = [];
		var tiers = m.t || [];
		var longest = Math.max(m.w || 0, m.h || 0);
		for (var i = 0; i < tiers.length; i++) list.push({ id: tiers[i], px: longest ? Math.min(tiers[i], longest) : tiers[i] });
		if (m.v) list.push({ id: 'v', px: Infinity });   // SVG: sharp at any size
		else if (!list.length && m.o) list.push({ id: 'o', px: longest || L.TOP });
		list.sort(function (a, b) { return a.px - b.px; });
		return list;
	};

	/* Megapixels the browser decodes to show source `src` for record `m` at `screenPx` long side. */
	L.cost = function (m, src, screenPx) {
		if (!src) return 0;
		var px = src.px === Infinity ? screenPx : src.px;
		if (!m.w || !m.h) return px * px / 1e6;   // size unknown (no preview could be made): assume square
		return Math.pow(Math.min(px, Math.max(m.w, m.h)), 2) * Math.min(m.w, m.h) / Math.max(m.w, m.h) / 1e6;
	};

	/* The source to show at `need` device pixels (long side), or null for a color box.
	   `current` (a source id) is kept within a band, so zooming around a threshold can't thrash. */
	L.choose = function (m, need, current) {
		if (need < L.COLOR_BOX_PX && m.c) return null;
		var list = L.sources(m);
		if (!list.length) return null;
		var pick = null;
		for (var i = 0; i < list.length; i++) if (list[i].px >= need) { pick = list[i]; break; }
		if (!pick) pick = list[list.length - 1];
		if (current != null) {
			for (var j = 0; j < list.length; j++) {
				var c = list[j];
				if (c.id === current && c.px >= need * L.KEEP_SMALLER && c.px <= Math.max(pick.px, need * L.KEEP_LARGER)) return c;
			}
		}
		return pick;
	};

	L.smaller = function (m, src) {
		var list = L.sources(m), prev = null;
		for (var i = 0; i < list.length; i++) {
			if (list[i].id === src.id) return prev;
			prev = list[i];
		}
		return null;
	};

	/* entries: [{m, area, need, src}] for every picture on screen. The largest on screen keep
	   their choice; once the budget is spent, the rest step down to smaller files or color. */
	L.applyBudget = function (entries, limitMP) {
		entries.sort(function (a, b) { return b.area - a.area; });
		var used = 0;
		for (var i = 0; i < entries.length; i++) {
			var e = entries[i], src = e.src;
			while (src && used + L.cost(e.m, src, e.need) > limitMP) src = L.smaller(e.m, src);
			e.src = src;
			used += L.cost(e.m, src, e.need);
		}
		return used;
	};
})(globalThis.MV = globalThis.MV || {});
