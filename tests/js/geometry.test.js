// Link shapes: paths, caps and boxes for connectors and mind map branches.
const test = require('node:test');
const assert = require('node:assert/strict');

require('../../miro_viewer/static/js/geometry.js');
const G = globalThis.MV.geom;

function numbers(d) { return d.match(/-?\d+(\.\d+)?/g).map(Number); }

test('a straight link runs between its end points, relative to its box', () => {
	const g = G.link({ a: [100, 100, 1, 0], b: [300, 100, -1, 0], sh: 'straight', w: 2 });
	const pad = 2 * 2 + G.capSize(2);
	assert.deepEqual(g.box, { x1: 100 - pad, y1: 100 - pad, x2: 300 + pad, y2: 100 + pad });
	assert.deepEqual(numbers(g.d), [pad, pad, 200 + pad, pad]);
	assert.deepEqual(g.caps, []);
});

test('a curved link leaves each end along its normal', () => {
	const r = G.route({ a: [0, 0, 1, 0], b: [100, 100, 0, -1], sh: 'curved', w: 2 });
	assert.equal(r.kind, 'cubic');
	assert.equal(r.pts[1][1], 0, 'first control point straight out to the right');
	assert.equal(r.pts[2][0], 100, 'last control point straight up from the end');
	assert.deepEqual(r.at(0), [0, 0]);
	assert.deepEqual(r.at(1), [100, 100]);
});

test('an elbowed link only turns at right angles', () => {
	const r = G.route({ a: [0, 0, 1, 0], b: [100, 60, -1, 0], sh: 'elbowed' });
	for (let i = 1; i < r.pts.length; i++) {
		const [p, q] = [r.pts[i - 1], r.pts[i]];
		assert.ok(p[0] === q[0] || p[1] === q[1], `segment ${i} is diagonal`);
	}
});

test('an arrow cap points at the end and the line stops short of its tip', () => {
	const g = G.link({ a: [0, 0, 1, 0], b: [200, 0, -1, 0], sh: 'straight', w: 4, s1: 'stealth' });
	assert.equal(g.caps.length, 1);
	const tip = numbers(g.caps[0].d).slice(0, 2);
	const lineEnd = numbers(g.d).slice(-2);
	assert.equal(tip[0], 200 - g.box.x1);
	assert.ok(lineEnd[0] < tip[0], 'line ends before the arrow tip');
});

test('unknown caps are left off', () => {
	assert.equal(G.cap('erd_many', [0, 0], [1, 0], 2), null);
});
