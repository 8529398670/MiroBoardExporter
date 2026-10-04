/* Sticky notes: a colored square with a soft shadow and auto-fitted text. */
(function (MV) {
	'use strict';

	var U = MV.util, R = MV.render;

	R.register('sticky_note', {
		create: function (rec) {
			var el = U.el('div', 'mv-note');
			el.style.background = rec.bg;
			R.textBox(rec, el, 0.08);
			MV.fitText.request(el, rec);
			return el;
		},
		update: R.greek
	});
})(globalThis.MV = globalThis.MV || {});
