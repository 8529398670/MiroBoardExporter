/* The board in the DOM, virtualized: only items near the screen exist as elements.

   Every view change runs one pass (at most once per animation frame): a flat scan of all item
   boxes against the view (well under a millisecond for the largest boards), mount what came
   near, unmount what drifted far, let renderers adjust their level of detail, then pick picture
   files within the decode budget. Mounting runs ahead of the screen and unmounting lags behind
   it, so a pan back and forth doesn't rebuild the same nodes. */
(function (MV) {
	'use strict';

	var U = MV.util, R = MV.render, L = MV.lod;

	var MOUNT_MARGIN = 0.35;    // share of the larger screen side mounted ahead of the view
	var KEEP_MARGIN = 1.0;      // ... and kept beyond it before unmounting
	var MIN_PX = 1.5;           // items smaller than this on screen aren't drawn
	var MOUNTS_PER_PASS = 250;  // spread big mounts over frames so a pan never stalls
	var REBASE_PX = 2500;
	var GRID = 20;              // background dot spacing in world units, scaled by powers of 5

	function Scene(opts) {
		this.stage = opts.stage;
		this.world = opts.world;
		this.cam = opts.cam;
		this.data = opts.data;
		this.root = opts.data.media || 'media/';
		this.budget = U.isTouch() ? 48 : 160;   // megapixels of decoded pictures
		this.dpr = Math.min(window.devicePixelRatio || 1, 3);
		this.mounted = new Map();
		this.indexById = {};
		this.selected = null;
		this.flashId = null;
		this.more = false;
		this.rebased = true;
		this.build();
		var self = this;
		this.schedule = U.rafThrottle(function () { self.update(); });
	}
	var P = Scene.prototype;

	/* Drawables in paint order: frames, mind map branches, items, connectors. */
	P.build = function () {
		var items = this.data.items, links = this.data.links || [];
		var list = [];
		var frames = items.filter(function (r) { return r.t === 'frame'; });
		var rest = items.filter(function (r) { return r.t !== 'frame'; });
		var branches = links.filter(function (l) { return l.k === 'm'; });
		var connectors = links.filter(function (l) { return l.k !== 'm'; });
		function addItem(rec) {
			var x1 = rec.x, y1 = rec.y, x2 = rec.x + rec.w, y2 = rec.y + rec.h;
			if (rec.r) {
				var a = rec.r * Math.PI / 180, c = Math.abs(Math.cos(a)), s = Math.abs(Math.sin(a));
				var hw = (rec.w * c + rec.h * s) / 2, hh = (rec.w * s + rec.h * c) / 2;
				var cx = rec.x + rec.w / 2, cy = rec.y + rec.h / 2;
				x1 = cx - hw; x2 = cx + hw; y1 = cy - hh; y2 = cy + hh;
			}
			list.push({ rec: rec, type: rec.t, renderer: R.get(rec.t), x1: x1, y1: y1, x2: x2, y2: y2 });
		}
		function addLink(rec) {
			rec._g = MV.geom.link(rec);
			var b = rec._g.box;
			list.push({ rec: rec, type: 'link', renderer: R.get('link'), x1: b.x1, y1: b.y1, x2: b.x2, y2: b.y2 });
		}
		frames.forEach(addItem);
		branches.forEach(addLink);
		rest.forEach(addItem);
		connectors.forEach(addLink);
		var self = this;
		list.forEach(function (d, i) {
			d.z = i + 1;
			d.size = Math.max(d.x2 - d.x1, d.y2 - d.y1);
			if (d.type !== 'link') self.indexById[d.rec.id] = i;
		});
		this.drawables = list;
	};

	P.record = function (id) {
		var i = this.indexById[id];
		return i == null ? null : this.drawables[i].rec;
	};

	/* World box of an item (rotation included). */
	P.box = function (id) {
		var i = this.indexById[id];
		if (i == null) return null;
		var d = this.drawables[i];
		return { x1: d.x1, y1: d.y1, x2: d.x2, y2: d.y2 };
	};

	P.element = function (id) {
		var i = this.indexById[id];
		return i == null ? null : this.mounted.get(i) || null;
	};

	function hits(d, r) { return d.x2 >= r.x1 && d.x1 <= r.x2 && d.y2 >= r.y1 && d.y1 <= r.y2; }

	P.position = function (el, d, ctx) {
		var rec = d.rec, st = el.style;
		st.zIndex = d.z;
		if (d.type === 'link') {
			st.left = (d.x1 - ctx.ox) + 'px';
			st.top = (d.y1 - ctx.oy) + 'px';
			return;
		}
		st.left = (rec.x - ctx.ox) + 'px';
		st.width = rec.w + 'px';
		var rot = rec.r ? ' rotate(' + rec.r + 'deg)' : '';
		if (rec.ac) {
			st.top = (rec.y + rec.h / 2 - ctx.oy) + 'px';
			st.transform = 'translateY(-50%)' + rot;
		} else {
			st.top = (rec.y - ctx.oy) + 'px';
			st.height = rec.h + 'px';
			if (rot) st.transform = rot.trim();
		}
	};

	P.mount = function (i, ctx) {
		var d = this.drawables[i];
		var el = d.renderer.create(d.rec, ctx);
		el.classList.add('mv-item');
		if (d.type !== 'link') el.setAttribute('data-id', d.rec.id);
		ctx.z = d.z;
		if (d.renderer.place) d.renderer.place(el, d.rec, ctx);
		else this.position(el, d, ctx);
		if (d.rec.id === this.selected) el.classList.add('mv-sel');
		if (d.rec.id === this.flashId) this.flashEl(el);
		this.world.appendChild(el);
		this.mounted.set(i, el);
	};

	P.unmount = function (i, el) {
		var d = this.drawables[i];
		if (d.renderer.release) d.renderer.release(el);
		el.remove();
		this.mounted.delete(i);
	};

	P.update = function () {
		var cam = this.cam, s = cam.s, self = this;
		if (cam.needsRebase(REBASE_PX)) { cam.rebase(); this.rebased = true; }
		this.world.style.transform = cam.worldTransform();
		this.updateGrid();

		var side = Math.max(cam.W, cam.H);
		var mountView = cam.viewRect(side * MOUNT_MARGIN), keepView = cam.viewRect(side * KEEP_MARGIN);
		var view = cam.viewRect(0);
		var ctx = { s: s, ox: cam.ox, oy: cam.oy, view: view, z: 0, scene: this };
		var list = this.drawables;

		this.mounted.forEach(function (el, i) {
			var d = list[i];
			if (!hits(d, keepView) || d.size * s < MIN_PX * 0.5) self.unmount(i, el);
		});

		var mounts = 0;
		for (var i = 0; i < list.length; i++) {
			var d = list[i];
			if (d.size * s < MIN_PX || this.mounted.has(i) || !hits(d, mountView)) continue;
			if (mounts++ >= MOUNTS_PER_PASS) { this.more = true; break; }
			this.mount(i, ctx);
		}

		var pictures = [], dpr = this.dpr, rebased = this.rebased;
		this.mounted.forEach(function (el, i) {
			var d = list[i], r = d.renderer;
			ctx.z = d.z;
			if (r.place) r.place(el, d.rec, ctx);
			else if (rebased) self.position(el, d, ctx);
			if (r.update) r.update(el, d.rec, ctx);
			if (r.media && el._media) {
				var need = Math.max(d.rec.w, d.rec.h) * s * dpr;
				var vis = Math.max(0, Math.min(d.x2, view.x2) - Math.max(d.x1, view.x1)) *
					Math.max(0, Math.min(d.y2, view.y2) - Math.max(d.y1, view.y1)) * s * s;
				pictures.push({ el: el, m: el._media, need: need, area: vis, src: L.choose(el._media, need, el._srcId) });
			}
		});
		this.rebased = false;
		this.ringWidth(s);
		L.applyBudget(pictures, this.budget);
		for (var p = 0; p < pictures.length; p++) R.setPicture(pictures[p].el, pictures[p].m, pictures[p].src, this.root);

		if (this.more) { this.more = false; this.schedule(); }
		if (this.onUpdate) this.onUpdate();
	};

	/* Background dots on a world grid whose spacing steps by 5x to stay 12-60 px on screen. */
	P.updateGrid = function () {
		var cam = this.cam, g = GRID * cam.s;
		if (!(g > 0)) return;
		while (g < 12) g *= 5;
		while (g > 60) g /= 5;
		var st = this.stage.style;
		st.setProperty('--grid', g.toFixed(3) + 'px');
		st.setProperty('--gx', (((cam.x % g) + g) % g).toFixed(2) + 'px');
		st.setProperty('--gy', (((cam.y % g) + g) % g).toFixed(2) + 'px');
	};

	/* Selection and flash rings are drawn in board units: keep them a few screen pixels wide. */
	P.ringWidth = function (s) {
		var ring = (2.5 / s) + 'px';
		[this.selected, this.flashId].forEach(function (id) {
			var el = id && this.element(id);
			if (!el) return;
			el.style.setProperty('--ring', ring);
			el.style.setProperty('--ring-gap', ring);
			el.style.outlineWidth = ring;
			el.style.outlineOffset = ring;
		}, this);
	};

	P.select = function (id) {
		var old = this.selected && this.element(this.selected);
		if (old) old.classList.remove('mv-sel');
		this.selected = id;
		var el = id && this.element(id);
		if (el) el.classList.add('mv-sel');
		this.ringWidth(this.cam.s);
	};

	/* Briefly ring an item (search result, deep link). Applied on mount if it isn't drawn yet. */
	P.flash = function (id) {
		this.flashId = id;
		var el = this.element(id);
		if (el) this.flashEl(el);
		this.ringWidth(this.cam.s);
	};

	P.flashEl = function (el) {
		var self = this;
		el.classList.remove('mv-flash');
		void el.offsetWidth;
		el.classList.add('mv-flash');
		setTimeout(function () { el.classList.remove('mv-flash'); if (self.flashId && el.getAttribute('data-id') === self.flashId) self.flashId = null; }, 2200);
	};

	/* The item under a screen point: the topmost element with an id, ignoring frame backgrounds. */
	P.itemAt = function (sx, sy) {
		var el = document.elementFromPoint(sx, sy);
		var hit = el && el.closest && el.closest('[data-id]');
		if (!hit || !this.world.contains(hit)) return null;
		return { id: hit.getAttribute('data-id'), el: el };
	};

	MV.Scene = Scene;
})(globalThis.MV = globalThis.MV || {});
