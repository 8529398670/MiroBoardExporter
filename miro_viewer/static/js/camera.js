/* The view onto the board: screen = world * s + (x, y). Pure math, no DOM.

   Boards are huge (one spans 900,000 units), and both a CSS translate of millions of pixels
   and node positions in the hundreds of thousands lose precision in the compositor's float32
   math: zoomed in on a far corner, items visibly jitter. So nodes are placed relative to a
   local origin (ox, oy) that is moved near the view whenever it drifts far off screen, and
   #world is translated to that origin's screen position: every number stays small. */
(function (MV) {
	'use strict';

	function Camera(opts) {
		opts = opts || {};
		this.x = 0; this.y = 0; this.s = 1;
		this.min = opts.min || 0.0002;
		this.max = opts.max || 40;
		this.W = 1; this.H = 1;
		this.ox = 0; this.oy = 0;
		this.bounds = opts.bounds || null;   // [x1, y1, x2, y2]
	}
	var P = Camera.prototype;

	P.setViewport = function (W, H) { this.W = Math.max(1, W); this.H = Math.max(1, H); };

	P.toScreen = function (wx, wy) { return { x: wx * this.s + this.x, y: wy * this.s + this.y }; };
	P.toWorld = function (sx, sy) { return { x: (sx - this.x) / this.s, y: (sy - this.y) / this.s }; };

	P.clampScale = function (s) { return Math.min(this.max, Math.max(this.min, s)); };

	P.zoomAt = function (sx, sy, factor) {
		var s2 = this.clampScale(this.s * factor);
		var f = s2 / this.s;
		this.x = sx - (sx - this.x) * f;
		this.y = sy - (sy - this.y) * f;
		this.s = s2;
	};

	P.panBy = function (dx, dy) { this.x += dx; this.y += dy; };

	P.get = function () { return { x: this.x, y: this.y, s: this.s }; };
	P.set = function (v) { this.x = v.x; this.y = v.y; this.s = v.s; };

	/* The world rectangle on screen, grown by `margin` screen pixels on every side. */
	P.viewRect = function (margin) {
		var m = (margin || 0) / this.s;
		var a = this.toWorld(0, 0), b = this.toWorld(this.W, this.H);
		return { x1: a.x - m, y1: a.y - m, x2: b.x + m, y2: b.y + m };
	};

	P.center = function () { return this.toWorld(this.W / 2, this.H / 2); };

	/* The view that fits world rect r inside the screen minus `pad` ({t, r, b, l} pixels). */
	P.fitView = function (r, pad, maxScale) {
		pad = pad || { t: 0, r: 0, b: 0, l: 0 };
		var w = Math.max(r.x2 - r.x1, 1e-6), h = Math.max(r.y2 - r.y1, 1e-6);
		var aw = Math.max(this.W - pad.l - pad.r, 40), ah = Math.max(this.H - pad.t - pad.b, 40);
		var s = Math.min(aw / w, ah / h);
		if (maxScale) s = Math.min(s, maxScale);
		s = this.clampScale(s);
		return {
			s: s,
			x: pad.l + (aw - w * s) / 2 - r.x1 * s,
			y: pad.t + (ah - h * s) / 2 - r.y1 * s
		};
	};

	/* Keep a sliver of the board on screen: an infinite canvas otherwise lets one flick
	   strand you in empty space with nothing to aim at. Returns true when it moved the view. */
	P.keepInView = function (keep) {
		var b = this.bounds;
		if (!b) return false;
		var left = this.x + b[0] * this.s, right = this.x + b[2] * this.s;
		var top = this.y + b[1] * this.s, bottom = this.y + b[3] * this.s;
		var kx = Math.min(keep, (right - left) / 2), ky = Math.min(keep, (bottom - top) / 2), hit = false;
		if (right < kx) { this.x += kx - right; hit = true; }
		else if (left > this.W - kx) { this.x -= left - (this.W - kx); hit = true; }
		if (bottom < ky) { this.y += ky - bottom; hit = true; }
		else if (top > this.H - ky) { this.y -= top - (this.H - ky); hit = true; }
		return hit;
	};

	/* True when the local origin sits so far from the screen that positions grow large. */
	P.needsRebase = function (limitPx) {
		var o = this.toScreen(this.ox, this.oy);
		return Math.abs(o.x - this.W / 2) > limitPx || Math.abs(o.y - this.H / 2) > limitPx;
	};

	P.rebase = function () {
		var c = this.center();
		this.ox = Math.round(c.x);
		this.oy = Math.round(c.y);
	};

	/* The CSS transform for #world, whose children sit at (world - origin). */
	P.worldTransform = function () {
		var o = this.toScreen(this.ox, this.oy);
		return 'translate(' + o.x + 'px,' + o.y + 'px) scale(' + this.s + ')';
	};

	/* Frame e (0..1) of a flight from view `a` to view `b`. The scale changes geometrically, and
	   dips out on long trips so the destination comes into sight before zooming in on it. */
	Camera.flightFrame = function (a, b, e, W, H) {
		var ca = { x: (W / 2 - a.x) / a.s, y: (H / 2 - a.y) / a.s };
		var cb = { x: (W / 2 - b.x) / b.s, y: (H / 2 - b.y) / b.s };
		var dist = Math.hypot(cb.x - ca.x, cb.y - ca.y);
		var both = Math.min(W, H) / Math.max(dist * 1.2, 1e-9);
		var low = Math.min(a.s, b.s);
		var dip = both < low ? Math.log(low / both) : 0;
		var s = Math.exp(Math.log(a.s) + (Math.log(b.s) - Math.log(a.s)) * e - dip * 4 * e * (1 - e));
		var cx = ca.x + (cb.x - ca.x) * e, cy = ca.y + (cb.y - ca.y) * e;
		return { s: s, x: W / 2 - cx * s, y: H / 2 - cy * s };
	};

	Camera.ease = function (k) { return k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2; };

	MV.Camera = Camera;
})(globalThis.MV = globalThis.MV || {});
