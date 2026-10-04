/* Shapes of connectors and mind map branches. Pure math, no DOM.

   A link record has a/b: [x, y, nx, ny], the end points and the direction the line leaves each
   item in (computed by the builder from Miro's % positions), plus sh: curved | straight |
   elbowed, w: stroke width and s0/s1: end caps. */
(function (MV) {
	'use strict';

	var G = MV.geom = {};

	function unit(x, y) {
		var l = Math.hypot(x, y);
		return l > 1e-9 ? [x / l, y / l] : [0, 0];
	}

	function cubicAt(p, t) {
		var u = 1 - t;
		return [
			u * u * u * p[0][0] + 3 * u * u * t * p[1][0] + 3 * u * t * t * p[2][0] + t * t * t * p[3][0],
			u * u * u * p[0][1] + 3 * u * u * t * p[1][1] + 3 * u * t * t * p[2][1] + t * t * t * p[3][1]
		];
	}

	function polyAt(pts, t) {
		var total = 0, lens = [];
		for (var i = 1; i < pts.length; i++) {
			var l = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
			lens.push(l);
			total += l;
		}
		var target = total * t;
		for (var j = 0; j < lens.length; j++) {
			if (target <= lens[j] || j === lens.length - 1) {
				var k = lens[j] ? Math.min(1, target / lens[j]) : 0;
				return [pts[j][0] + (pts[j + 1][0] - pts[j][0]) * k, pts[j][1] + (pts[j + 1][1] - pts[j][1]) * k];
			}
			target -= lens[j];
		}
		return pts[0];
	}

	/* Arrow length for a stroke width: Miro's heads grow with the line. */
	G.capSize = function (w) { return 8 + w * 3; };

	/* An end cap at `tip`, pointing along unit direction `dir`. Returns {d, fill, back}: back is
	   how far the line must stop short of the tip so a thick stroke doesn't poke through. */
	G.cap = function (kind, tip, dir, w) {
		var L = G.capSize(w), x = tip[0], y = tip[1], dx = dir[0], dy = dir[1];
		var px = -dy, py = dx;  // perpendicular
		function pt(along, side) { return (x - dx * along + px * side).toFixed(2) + ' ' + (y - dy * along + py * side).toFixed(2); }
		var half = L * 0.45;
		switch (kind) {
			case 'stealth':
			case 'rounded_stealth':
				return { d: 'M' + pt(0, 0) + 'L' + pt(L, half) + 'L' + pt(L * 0.7, 0) + 'L' + pt(L, -half) + 'Z', fill: true, back: L * 0.7 };
			case 'filled_triangle':
				return { d: 'M' + pt(0, 0) + 'L' + pt(L, half) + 'L' + pt(L, -half) + 'Z', fill: true, back: L };
			case 'triangle':
				return { d: 'M' + pt(0, 0) + 'L' + pt(L, half) + 'L' + pt(L, -half) + 'Z', fill: false, back: L };
			case 'arrow':
				return { d: 'M' + pt(L, half) + 'L' + pt(0, 0) + 'L' + pt(L, -half), fill: false, back: 0 };
			case 'filled_diamond':
			case 'diamond':
				return { d: 'M' + pt(0, 0) + 'L' + pt(L / 2, half) + 'L' + pt(L, 0) + 'L' + pt(L / 2, -half) + 'Z', fill: kind === 'filled_diamond', back: L };
			case 'filled_oval':
			case 'oval':
				var r = L * 0.3, cx = x - dx * r, cy = y - dy * r, rs = r.toFixed(2);
				return { d: 'M' + (cx - r).toFixed(2) + ' ' + cy.toFixed(2) + 'a' + rs + ' ' + rs + ' 0 1 0 ' + (2 * r).toFixed(2) + ' 0a' + rs + ' ' + rs + ' 0 1 0 ' + (-2 * r).toFixed(2) + ' 0', fill: kind === 'filled_oval', back: 2 * r };
			default:
				return null;
		}
	};

	/* The control points of a link, in world coordinates, plus a point-at-t function. */
	G.route = function (link) {
		var a = link.a, b = link.b;
		var A = [a[0], a[1]], B = [b[0], b[1]];
		var na = unit(a[2], a[3]), nb = unit(b[2], b[3]);
		var toward = unit(B[0] - A[0], B[1] - A[1]);
		if (!na[0] && !na[1]) na = toward;
		if (!nb[0] && !nb[1]) nb = [-toward[0], -toward[1]];
		var dist = Math.hypot(B[0] - A[0], B[1] - A[1]);

		if (link.sh === 'curved') {
			var k = Math.max(dist * 0.4, (link.w || 2) * 6);
			var p = [A, [A[0] + na[0] * k, A[1] + na[1] * k], [B[0] + nb[0] * k, B[1] + nb[1] * k], B];
			return {
				kind: 'cubic', pts: p,
				at: function (t) { return cubicAt(p, t); },
				startDir: unit(p[0][0] - p[1][0], p[0][1] - p[1][1]),
				endDir: unit(p[3][0] - p[2][0], p[3][1] - p[2][1])
			};
		}
		var pts;
		if (link.sh === 'elbowed') {
			var ha = Math.abs(na[0]) >= Math.abs(na[1]), hb = Math.abs(nb[0]) >= Math.abs(nb[1]);
			if (ha && hb) { var mx = (A[0] + B[0]) / 2; pts = [A, [mx, A[1]], [mx, B[1]], B]; }
			else if (!ha && !hb) { var my = (A[1] + B[1]) / 2; pts = [A, [A[0], my], [B[0], my], B]; }
			else if (ha) pts = [A, [B[0], A[1]], B];
			else pts = [A, [A[0], B[1]], B];
		} else {
			pts = [A, B];
		}
		var n = pts.length;
		return {
			kind: 'poly', pts: pts,
			at: function (t) { return polyAt(pts, t); },
			startDir: unit(pts[0][0] - pts[1][0], pts[0][1] - pts[1][1]),
			endDir: unit(pts[n - 1][0] - pts[n - 2][0], pts[n - 1][1] - pts[n - 2][1])
		};
	};

	/* Everything needed to draw a link: path and caps relative to the link's world box. */
	G.link = function (link) {
		var r = G.route(link), w = link.w || 2;
		var xs = [], ys = [];
		r.pts.forEach(function (p) { xs.push(p[0]); ys.push(p[1]); });
		var pad = w * 2 + G.capSize(w);
		var box = { x1: Math.min.apply(null, xs) - pad, y1: Math.min.apply(null, ys) - pad, x2: Math.max.apply(null, xs) + pad, y2: Math.max.apply(null, ys) + pad };
		var pts = r.pts.map(function (p) { return [p[0] - box.x1, p[1] - box.y1]; });

		var last = pts.length - 1, caps = [];
		var start = G.cap(link.s0, pts[0], r.startDir, w);
		var end = G.cap(link.s1, pts[last], r.endDir, w);
		if (start) { caps.push(start); pts[0] = [pts[0][0] - r.startDir[0] * start.back * 0.9, pts[0][1] - r.startDir[1] * start.back * 0.9]; }
		if (end) { caps.push(end); pts[last] = [pts[last][0] - r.endDir[0] * end.back * 0.9, pts[last][1] - r.endDir[1] * end.back * 0.9]; }

		function f(p) { return p[0].toFixed(2) + ' ' + p[1].toFixed(2); }
		var d = 'M' + f(pts[0]);
		if (r.kind === 'cubic') d += 'C' + f(pts[1]) + ' ' + f(pts[2]) + ' ' + f(pts[3]);
		else for (var i = 1; i < pts.length; i++) d += 'L' + f(pts[i]);

		return { d: d, caps: caps, box: box, at: r.at };
	};
})(globalThis.MV = globalThis.MV || {});
