// Camera math: zooming keeps the point under the finger fixed, fitting centers a rect, and the
// local origin keeps the numbers the browser sees small.
const test = require('node:test');
const assert = require('node:assert/strict');

require('../../miro_viewer/static/js/camera.js');
const Camera = globalThis.MV.Camera;

function close(a, b, eps = 1e-6) { assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`); }

test('zoomAt keeps the world point under the screen point', () => {
	const cam = new Camera({ min: 0.001, max: 50 });
	cam.setViewport(400, 800);
	cam.set({ x: 10, y: -20, s: 0.5 });
	const before = cam.toWorld(123, 456);
	cam.zoomAt(123, 456, 3);
	const after = cam.toWorld(123, 456);
	close(before.x, after.x);
	close(before.y, after.y);
	close(cam.s, 1.5);
});

test('zoom is clamped to the camera limits', () => {
	const cam = new Camera({ min: 0.1, max: 2 });
	cam.zoomAt(0, 0, 100);
	assert.equal(cam.s, 2);
	cam.zoomAt(0, 0, 1e-6);
	assert.equal(cam.s, 0.1);
});

test('fitView centers a rect inside the padded screen', () => {
	const cam = new Camera();
	cam.setViewport(400, 800);
	const v = cam.fitView({ x1: 1000, y1: 1000, x2: 3000, y2: 2000 }, { t: 100, r: 0, b: 100, l: 0 });
	close(v.s, 0.2);   // width-bound: 400 / 2000
	cam.set(v);
	const c = cam.toWorld(200, 100 + 300);   // middle of the padded area
	close(c.x, 2000);
	close(c.y, 1500);
});

test('rebasing moves the local origin to the view without moving the view', () => {
	const cam = new Camera();
	cam.setViewport(400, 800);
	cam.set({ x: -800000 * 4 + 200, y: -400000 * 4 + 400, s: 4 });   // looking at (800000, 400000)
	assert.ok(cam.needsRebase(2500));
	const before = cam.center();
	cam.rebase();
	assert.ok(!cam.needsRebase(2500));
	assert.deepEqual([cam.ox, cam.oy], [800000, 400000]);
	const after = cam.center();
	close(before.x, after.x);
	// the world layer is translated by small numbers only
	const m = /translate\((-?[\d.]+)px,(-?[\d.]+)px\)/.exec(cam.worldTransform());
	assert.ok(Math.abs(+m[1]) < 1000 && Math.abs(+m[2]) < 1000, cam.worldTransform());
});

test('keepInView pulls a lost board back to the screen edge', () => {
	const cam = new Camera({ bounds: [0, 0, 1000, 1000] });
	cam.setViewport(400, 800);
	cam.set({ x: 5000, y: 0, s: 1 });   // board far off to the right
	assert.ok(cam.keepInView(110));
	assert.equal(cam.x, 400 - 110);
});

test('a flight starts and ends on its views', () => {
	const a = { x: 0, y: 0, s: 1 }, b = { x: -5000, y: -3000, s: 2 };
	const start = Camera.flightFrame(a, b, 0, 400, 800), end = Camera.flightFrame(a, b, 1, 400, 800);
	for (const k of ['x', 'y', 's']) { close(start[k], a[k], 1e-6); close(end[k], b[k], 1e-6); }
	const mid = Camera.flightFrame(a, b, 0.5, 400, 800);
	assert.ok(mid.s < 1, 'zooms out on a long trip');
});
