/* MV.boot(data) is called by the board's data.js, the last script on the page. It wires the
   camera, scene and gestures to the UI and picks the opening view: an item or view named in the
   address, else where this board was left, else the whole board. */
(function (MV) {
	'use strict';

	var U = MV.util;
	var FLIGHT_MS = 650;

	MV.boot = function (data) {
		var ui = MV.ui, panels = ui.panels;
		var stage = U.$('stage'), world = U.$('world');
		var b = data.bounds;
		var boardRect = { x1: b[0], y1: b[1], x2: b[2], y2: b[3] };
		var cam = new MV.Camera({ bounds: b });
		cam.setViewport(window.innerWidth, window.innerHeight);
		var scene = new MV.Scene({ stage: stage, world: world, cam: cam, data: data });
		var app = MV.app = { data: data, cam: cam, scene: scene, currentFrame: -1 };
		var flight = null;

		U.$('loading').hidden = true;
		if (!data.items.length) U.$('empty').hidden = false;

		/* Zoom out to a few times smaller than the whole board, in to 40x. */
		function setLimits() {
			var fit = cam.fitView(boardRect, null).s;
			cam.min = Math.min(fit / 4, 0.01);
		}
		cam.min = 1e-6;
		setLimits();

		function commit() { scene.schedule(); }
		scene.onUpdate = function () { ui.bar.update(cam); };

		/* Pixels the bars and open panels cover, so fitting keeps content out from under them. */
		function pads() {
			var top = U.$('topbar').getBoundingClientRect().bottom + 12;
			var dock = U.$('dock').getBoundingClientRect();
			var pad = { t: top, r: 16, b: Math.max(16, window.innerHeight - dock.top + 12), l: 16 };
			if (U.media('(min-width: 900px)')) {
				['framesPanel', 'searchPanel'].forEach(function (id) {
					if (panels.isOpen(id)) pad.l = Math.max(pad.l, U.$(id).getBoundingClientRect().right + 16);
				});
				if (panels.isOpen('sheet')) pad.r = Math.max(pad.r, window.innerWidth - U.$('sheet').getBoundingClientRect().left + 16);
			}
			return pad;
		}

		function stopFlight() {
			if (flight) { cancelAnimationFrame(flight); flight = null; stage.classList.remove('interacting'); }
		}

		app.flyTo = function (target, animate) {
			stopFlight();
			if (!animate || U.reducedMotion()) { cam.set(target); commit(); settle(); return; }
			var from = cam.get(), t0 = performance.now();
			stage.classList.add('interacting');
			(function step(now) {
				var k = Math.min(1, (now - t0) / FLIGHT_MS);
				cam.set(k < 1 ? MV.Camera.flightFrame(from, target, MV.Camera.ease(k), cam.W, cam.H) : target);
				scene.update();
				if (k < 1) flight = requestAnimationFrame(step);
				else { flight = null; stage.classList.remove('interacting'); settle(); }
			})(t0);
		};

		app.fitBoard = function (animate) {
			if (!data.items.length) { cam.set({ x: cam.W / 2, y: cam.H / 2, s: 1 }); commit(); return; }
			app.flyTo(cam.fitView(boardRect, pads()), animate);
		};

		app.zoomBy = function (f, sx, sy) {
			stopFlight();
			var target = cam.get(), s2 = cam.clampScale(cam.s * f), k = s2 / cam.s;
			sx = sx == null ? cam.W / 2 : sx;
			sy = sy == null ? cam.H / 2 : sy;
			target.x = sx - (sx - cam.x) * k;
			target.y = sy - (sy - cam.y) * k;
			target.s = s2;
			app.flyTo(target, true);
		};

		app.goFrame = function (i, animate) {
			var id = data.frames[i];
			var box = id && scene.box(id);
			if (!box) return;
			app.currentFrame = i;
			ui.frames.setCurrent(i);
			app.flyTo(cam.fitView(box, pads()), animate);
		};

		app.stepFrame = function (d) {
			var n = data.frames.length;
			if (!n) return;
			var i = app.currentFrame < 0 ? (d > 0 ? 0 : n - 1) : U.clamp(app.currentFrame + d, 0, n - 1);
			app.goFrame(i, true);
		};

		/* Bring an item to the middle of the screen: fitted if big, at no less than 100% if small. */
		app.focusItem = function (id, opts) {
			opts = opts || {};
			var box = scene.box(id);
			if (!box) return;
			var mx = (box.x2 - box.x1) * 0.15, my = (box.y2 - box.y1) * 0.15;
			var grown = { x1: box.x1 - mx, y1: box.y1 - my, x2: box.x2 + mx, y2: box.y2 + my };
			var view = cam.fitView(grown, pads(), Math.max(1, Math.min(cam.s, 4)));
			if (opts.flash) scene.flash(id);
			if (opts.select) select(id);
			app.flyTo(view, opts.animate !== false);
		};

		function select(id) {
			var rec = scene.record(id);
			if (!rec) return;
			scene.select(id);
			ui.sheet.show(rec);
			ui.hash.item(id);
		}

		app.deselect = function () {
			scene.select(null);
			saveView();
		};

		/* The frame under the middle of the screen (the smallest, if they nest). */
		function frameAtCenter() {
			var c = cam.center(), best = -1, area = Infinity;
			data.frames.forEach(function (id, i) {
				var r = scene.box(id);
				if (r && c.x >= r.x1 && c.x <= r.x2 && c.y >= r.y1 && c.y <= r.y2) {
					var a = (r.x2 - r.x1) * (r.y2 - r.y1);
					if (a < area) { area = a; best = i; }
				}
			});
			return best;
		}

		function currentView() {
			var c = cam.center();
			return { cx: c.x, cy: c.y, s: cam.s };
		}

		var saveView = U.debounce(function () {
			var v = currentView();
			ui.viewstate.save(app, v);
			if (!scene.selected) {
				var o = data.origin || [0, 0];
				ui.hash.view({ cx: v.cx + o[0], cy: v.cy + o[1], s: v.s });
			}
		}, 400);

		function settle() {
			var i = frameAtCenter();
			if (i >= 0 || app.currentFrame >= 0) {
				if (i !== app.currentFrame && i >= 0) { app.currentFrame = i; }
				ui.frames.setCurrent(app.currentFrame);
			}
			saveView();
		}

		function viewAt(v) {
			return { s: cam.clampScale(v.s), x: cam.W / 2 - v.cx * cam.clampScale(v.s), y: cam.H / 2 - v.cy * cam.clampScale(v.s) };
		}

		function tap(sx, sy) {
			var hit = scene.itemAt(sx, sy);
			var link = hit && hit.el.closest && hit.el.closest('a[href]');
			if (link) { window.open(link.href, '_blank', 'noopener'); return; }
			var rec = hit && scene.record(hit.id);
			if (rec && rec.t === 'frame') {
				if (hit.el.closest('.mv-frame-title')) app.goFrame(data.frames.indexOf(rec.id), true);
				else rec = null;
			}
			if (!rec) { ui.sheet.hide(); return; }
			select(rec.id);
		}

		MV.gestures(stage, cam, {
			onChange: commit,
			onTap: tap,
			onStop: stopFlight,
			onSettle: U.debounce(settle, 250),
			onInteract: function (on) { if (!on) settle(); },
			zoomAt: function (x, y, f) { app.zoomBy(f, x, y); }
		});

		window.addEventListener('resize', function () {
			var c = cam.center();
			cam.setViewport(window.innerWidth, window.innerHeight);
			setLimits();
			cam.x = cam.W / 2 - c.x * cam.s;
			cam.y = cam.H / 2 - c.y * cam.s;
			commit();
		});

		window.addEventListener('keydown', function (e) {
			var typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || '');
			if (e.key === 'Escape') {
				if (ui.lightbox.isOpen()) ui.lightbox.close();
				else panels.closeTop();
				return;
			}
			if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
			if (e.key === '/') { e.preventDefault(); panels.open('searchPanel'); ui.search.focus(); }
			else if (e.key === '0') app.fitBoard(true);
			else if (e.key === '+' || e.key === '=') app.zoomBy(1.6);
			else if (e.key === '-' || e.key === '_') app.zoomBy(1 / 1.6);
			else if (e.key === 'ArrowRight' || e.key === 'PageDown') app.stepFrame(1);
			else if (e.key === 'ArrowLeft' || e.key === 'PageUp') app.stepFrame(-1);
			else return;
			e.preventDefault();
		});

		window.addEventListener('hashchange', function () {
			var h = ui.hash.parse();
			if (h.item && scene.box(h.item)) app.focusItem(h.item, { flash: true, select: true });
		});

		panels.init();
		ui.bar.init(app);
		ui.frames.init(app);
		ui.search.init(app);
		ui.sheet.init(app);
		ui.lightbox.init(app);
		ui.frames.setCurrent(-1);

		var start = ui.hash.parse(), o = data.origin || [0, 0];
		if (start.item && scene.box(start.item)) {
			app.focusItem(start.item, { flash: true, select: true, animate: false });
		} else if (start.view) {
			cam.set(viewAt({ cx: start.view.cx - o[0], cy: start.view.cy - o[1], s: start.view.s }));
			commit();
			settle();
		} else {
			var saved = ui.viewstate.load(app);
			if (saved) { cam.set(viewAt(saved)); commit(); settle(); }
			else app.fitBoard(false);
		}
	};
})(globalThis.MV = globalThis.MV || {});
