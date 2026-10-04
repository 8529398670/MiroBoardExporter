/* Touch, mouse, trackpad and wheel input on the board (ported from the Freeform viewer).

   One finger or the mouse pans, with a capped glide on release; two fingers pinch about their
   midpoint and follow it as it moves; a double tap or double click zooms in. A physical wheel
   zooms, a trackpad's two-finger scroll pans and its pinch (ctrl+wheel) zooms. A press that
   ends near where it started is a tap, reported to onTap. */
(function (MV) {
	'use strict';

	var MAX_FLING = 3.0;     // px per ms, about as fast as a real flick
	var DOUBLE_TAP_MS = 300;

	MV.gestures = function (stage, cam, h) {
		var pointers = new Map();
		var last = null, pinch = null, moved = 0, origin = null, touchy = false;
		var vx = 0, vy = 0, vt = 0, glide = null, lastTap = null;

		function interacting(on) {
			stage.classList.toggle('interacting', on);
			if (h.onInteract) h.onInteract(on);
		}
		function stopGlide() {
			if (glide) { cancelAnimationFrame(glide); glide = null; }
			if (h.onStop) h.onStop();
		}
		function snapshot() {
			var p = Array.from(pointers.values());
			return { dist: Math.hypot(p[1].x - p[0].x, p[1].y - p[0].y) || 1, mx: (p[0].x + p[1].x) / 2, my: (p[0].y + p[1].y) / 2 };
		}
		function changed() { h.onChange(); }

		stage.addEventListener('pointerdown', function (e) {
			if (e.button > 0) return;
			stopGlide();
			try { stage.setPointerCapture(e.pointerId); } catch (err) { /* synthetic events */ }
			pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
			interacting(true);
			if (pointers.size === 1) {
				moved = 0;
				origin = { x: e.clientX, y: e.clientY };
				touchy = e.pointerType !== 'mouse';
				last = { x: e.clientX, y: e.clientY };
				stage.classList.add('dragging');
				vx = vy = 0; vt = performance.now();
			} else if (pointers.size === 2) {
				pinch = snapshot();
				last = null;
				moved = 999;   // a pinch is never a tap
			}
		});

		stage.addEventListener('pointermove', function (e) {
			if (!pointers.has(e.pointerId)) return;
			pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
			if (pointers.size === 1 && last) {
				var dx = e.clientX - last.x, dy = e.clientY - last.y;
				/* Distance from the start, not the path: a shaky finger still taps. */
				if (origin) moved = Math.max(moved, Math.hypot(e.clientX - origin.x, e.clientY - origin.y));
				cam.panBy(dx, dy);
				/* Smoothed, so one sample over a tiny interval can't read as a huge speed. */
				var now = performance.now(), dt = Math.max(now - vt, 8);
				vx = vx * 0.7 + (dx / dt) * 0.3;
				vy = vy * 0.7 + (dy / dt) * 0.3;
				vt = now;
				last = { x: e.clientX, y: e.clientY };
				changed();
			} else if (pointers.size === 2 && pinch) {
				var now2 = snapshot();
				var s2 = cam.clampScale(cam.s * now2.dist / pinch.dist), f = s2 / cam.s;
				cam.x = now2.mx - (pinch.mx - cam.x) * f;
				cam.y = now2.my - (pinch.my - cam.y) * f;
				cam.s = s2;
				pinch = now2;
				changed();
			}
		});

		function coast() {
			var speed = Math.hypot(vx, vy);
			if (performance.now() - vt > 80 || speed < 0.05) { interacting(false); return; }
			if (speed > MAX_FLING) { vx *= MAX_FLING / speed; vy *= MAX_FLING / speed; }
			(function step() {
				cam.panBy(vx * 16, vy * 16);
				vx *= 0.94; vy *= 0.94;
				if (cam.keepInView(110)) vx = vy = 0;
				changed();
				if (Math.hypot(vx, vy) > 0.02) glide = requestAnimationFrame(step);
				else { glide = null; interacting(false); }
			})();
		}

		function release(e) {
			if (!pointers.has(e.pointerId)) return;
			pointers.delete(e.pointerId);
			if (pointers.size === 1) {
				pinch = null;
				var p = Array.from(pointers.values())[0];
				last = { x: p.x, y: p.y };
				vx = vy = 0;
			}
			if (pointers.size === 0) {
				stage.classList.remove('dragging');
				last = null; pinch = null;
				if (e.type === 'pointerup' && moved <= (touchy ? 14 : 6)) {
					interacting(false);
					var now = performance.now();
					if (touchy && lastTap && now - lastTap.t < DOUBLE_TAP_MS && Math.hypot(e.clientX - lastTap.x, e.clientY - lastTap.y) < 30) {
						lastTap = null;
						h.zoomAt(e.clientX, e.clientY, 2);
					} else {
						lastTap = { t: now, x: e.clientX, y: e.clientY };
						h.onTap(e.clientX, e.clientY, e);
					}
				} else {
					coast();
				}
			}
		}
		stage.addEventListener('pointerup', release);
		stage.addEventListener('pointercancel', release);

		stage.addEventListener('wheel', function (e) {
			e.preventDefault();
			stopGlide();
			var wheelish = e.deltaX === 0 && Math.abs(e.deltaY) >= 40 && Number.isInteger(e.deltaY) && e.deltaMode === 0;
			if (e.ctrlKey || e.metaKey || e.deltaMode === 1 || wheelish) {
				var d = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
				cam.zoomAt(e.clientX, e.clientY, Math.exp(-d * (e.ctrlKey && !wheelish ? 0.01 : 0.0022)));
			} else {
				cam.panBy(-e.deltaX, -e.deltaY);
				cam.keepInView(110);
			}
			changed();
			if (h.onSettle) h.onSettle();
		}, { passive: false });

		stage.addEventListener('dblclick', function (e) {
			if (e.pointerType === 'touch') return;
			h.zoomAt(e.clientX, e.clientY, 2);
		});

		/* iOS ignores user-scalable=no: without this a pinch on the bars zooms the whole page. */
		['gesturestart', 'gesturechange', 'gestureend'].forEach(function (name) {
			document.addEventListener(name, function (e) { e.preventDefault(); });
		});

		return { stop: stopGlide };
	};
})(globalThis.MV = globalThis.MV || {});
