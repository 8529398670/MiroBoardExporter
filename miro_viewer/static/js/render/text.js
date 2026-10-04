/* Text items: a fixed width and an automatic height, centered on the item's position. */
(function (MV) {
	'use strict';

	var U = MV.util, R = MV.render;

	R.register('text', {
		create: function (rec) {
			var el = U.el('div', 'mv-text');
			if (rec.bg) el.style.background = rec.bg;
			R.richText(rec, el);
			return el;
		},
		update: R.greek
	});
})(globalThis.MV = globalThis.MV || {});
