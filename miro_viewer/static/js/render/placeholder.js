/* Items Miro's API can't describe (tables, kanban boards, previews, ...) and any type this
   viewer doesn't know: a dashed box saying what was there. */
(function (MV) {
	'use strict';

	var U = MV.util, R = MV.render;

	R.typeName = function (type) {
		var s = String(type || 'item').replace(/_/g, ' ');
		return s.charAt(0).toUpperCase() + s.slice(1);
	};

	R.register('placeholder', {
		create: function (rec) {
			var el = U.el('div', 'mv-ph');
			el.style.fontSize = Math.max(6, Math.min(rec.h * 0.18, rec.w * 0.07, 28)) + 'px';
			U.el('div', 'mv-ph-type', el, R.typeName(rec.t));
			if (rec.title) U.el('div', 'mv-ph-title', el, rec.title);
			return el;
		}
	});
})(globalThis.MV = globalThis.MV || {});
