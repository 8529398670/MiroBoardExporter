/* Cards (and app cards): a white card with its theme color down the left edge. */
(function (MV) {
	'use strict';

	var U = MV.util, R = MV.render;

	R.register(['card', 'app_card'], {
		create: function (rec) {
			var el = U.el('div', 'mv-card');
			if (rec.bc) el.style.borderLeftColor = rec.bc;
			var body = U.el('div', 'mv-card-body', el);
			body.style.fontSize = Math.max(10, Math.min(rec.h * 0.28, 16)) + 'px';
			U.el('div', 'mv-card-title', body, rec.title || 'Card');
			if (rec.html) R.richText({ html: rec.html, fs: 0.85 * Math.max(10, Math.min(rec.h * 0.28, 16)) }, body, 'mv-card-desc');
			return el;
		}
	});
})(globalThis.MV = globalThis.MV || {});
