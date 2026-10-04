// Which picture file to show at a given size, and the decode budget.
const test = require('node:test');
const assert = require('node:assert/strict');

require('../../miro_viewer/static/js/lod.js');
const L = globalThis.MV.lod;

const big = { k: 'img/1', w: 4000, h: 3000, c: '#888888', t: [256, 1024, 2048], o: '../boards/b/assets/images/1.jpg' };
const small = { k: 'img/2', w: 800, h: 600, c: '#888888', t: [2048], o: '../boards/b/assets/images/2.png' };

test('tiny pictures are just their color', () => {
	assert.equal(L.choose(big, 20), null);
});

test('the smallest preview that covers the size wins; originals never go on the canvas', () => {
	assert.equal(L.choose(big, 200).id, 256);
	assert.equal(L.choose(big, 900).id, 1024);
	assert.equal(L.choose(big, 1500).id, 2048);
	assert.equal(L.choose(big, 9000).id, 2048);
});

test('the top preview of a small image is its own size', () => {
	assert.deepEqual(L.sources(small), [{ id: 2048, px: 800 }]);
	assert.equal(L.choose(small, 700).id, 2048);
});

test('svgs are always sharp; the original is used only when no preview could be made', () => {
	assert.equal(L.choose({ k: 'img/3', t: [], o: 'x.svg', v: 1 }, 5000).id, 'v');
	assert.equal(L.choose({ k: 'img/4', t: [], o: 'x.png' }, 500).id, 'o');
	assert.deepEqual(L.sources({ k: 'img/5', t: [], c: '#000' }), []);
});

test('the current file is kept within a band, so zooming near a threshold cannot thrash', () => {
	assert.equal(L.choose(big, 1100, 1024).id, 1024, 'slightly too small: keep');
	assert.equal(L.choose(big, 1500, 1024).id, 2048, 'much too small: upgrade');
	assert.equal(L.choose(big, 300, 1024).id, 1024, 'a bit larger than needed: keep');
	assert.equal(L.choose(big, 150, 2048).id, 256, 'far larger than needed: step down');
});

test('the budget keeps the largest pictures sharp and steps the rest down', () => {
	const entries = [
		{ m: big, area: 10, need: 1500, src: L.choose(big, 1500) },
		{ m: big, area: 1000, need: 1500, src: L.choose(big, 1500) },
	];
	const used = L.applyBudget(entries, 3.5);   // a 2048 tier is ~3.1 MP, a 1024 tier ~0.8 MP
	const byArea = entries.sort((a, b) => b.area - a.area);
	assert.equal(byArea[0].src.id, 2048);
	assert.equal(byArea[1].src.id, 256);
	assert.ok(used <= 3.5);
});
