/* The frame list (reading order, as numbered in the archive) and the "3 / 22" pager. Also lists
   the items Miro gives no position for, and says what couldn't be drawn. */
(function (MV) {
	'use strict';

	var U = MV.util;
	var ui = MV.ui = MV.ui || {};
	var built = false, current = -1, app = null;

	function build() {
		if (built) return;
		built = true;
		var list = U.$('frameList');
		app.data.frames.forEach(function (id, i) {
			var rec = app.scene.record(id);
			var li = U.el('li', '', list);
			var btn = U.el('button', 'row', li);
			btn.type = 'button';
			U.el('span', 'num', btn, String(i + 1));
			U.el('span', 'label', btn, (rec && rec.title) || 'Untitled frame');
			btn.addEventListener('click', function () {
				app.goFrame(i, true);
				ui.panels.closeTransient();
			});
		});
		if (current >= 0) mark(current);

		var unplaced = app.data.unplaced || [];
		if (unplaced.length) {
			U.$('unplacedSection').hidden = false;
			var ul = U.$('unplacedList');
			unplaced.forEach(function (u) {
				var li = U.el('li', 'row static', ul);
				U.el('span', 'num', li, MV.render.typeName(u.t));
				U.el('span', 'label', li, u.title || '(untitled)');
			});
		}
		var skipped = app.data.skipped || {};
		var names = Object.keys(skipped).map(function (t) { return skipped[t] + ' ' + MV.render.typeName(t).toLowerCase(); });
		if (names.length) {
			U.$('skippedNote').hidden = false;
			U.$('skippedNote').textContent = 'Not drawn (no shape or position in the archive): ' + names.join(', ') + '.';
		}
	}

	function mark(i) {
		var rows = U.$('frameList').children;
		for (var k = 0; k < rows.length; k++) rows[k].classList.toggle('current', k === i);
	}

	ui.frames = {
		init: function (a) { app = a; },

		reveal: function () {
			build();
			var row = U.$('frameList').children[current];
			if (row && row.scrollIntoView) row.scrollIntoView({ block: 'nearest' });
		},

		setCurrent: function (i) {
			current = i;
			var n = app.data.frames.length;
			U.$('frameLabel').textContent = n ? (i >= 0 ? (i + 1) + ' / ' + n : '– / ' + n) : '';
			U.$('prevFrame').disabled = i <= 0;
			U.$('nextFrame').disabled = i >= n - 1;
			if (built) mark(i);
		}
	};
})(globalThis.MV = globalThis.MV || {});
