/* Frames: a white page with its title above the top-left corner, at a constant screen size.

   One frame can be hundreds of thousands of units wide. Its background is cut down to the part
   near the screen, so the browser never paints or tiles a layer that size. */
(function (MV) {
	'use strict';

	var U = MV.util;
	var TITLE_PX = 13;      // title size on screen
	var CLIP_MARGIN = 400;  // screen pixels of background kept beyond the view

	MV.render.register('frame', {
		create: function (rec) {
			var el = U.el('div', 'mv-frame');
			el._bg = U.el('div', 'mv-frame-bg', el);
			if (rec.bg) el._bg.style.background = rec.bg;
			el._title = U.el('div', 'mv-frame-title', el, rec.title || ('Frame ' + (rec.n || '')));
			return el;
		},
		place: function (el, rec, ctx) {
			var view = ctx.view, m = CLIP_MARGIN / ctx.s;
			var x1 = Math.max(rec.x, view.x1 - m), y1 = Math.max(rec.y, view.y1 - m);
			var x2 = Math.min(rec.x + rec.w, view.x2 + m), y2 = Math.min(rec.y + rec.h, view.y2 + m);
			var st = el.style;
			st.left = (rec.x - ctx.ox) + 'px';
			st.top = (rec.y - ctx.oy) + 'px';
			st.zIndex = ctx.z;
			var bg = el._bg.style;
			bg.left = Math.max(0, x1 - rec.x) + 'px';
			bg.top = Math.max(0, y1 - rec.y) + 'px';
			bg.width = Math.max(0, x2 - x1) + 'px';
			bg.height = Math.max(0, y2 - y1) + 'px';

			var showTitle = rec.w * ctx.s > 60;
			if (showTitle !== el._showTitle) {
				el._showTitle = showTitle;
				el._title.style.display = showTitle ? '' : 'none';
			}
			if (showTitle) {
				var k = 1 / ctx.s;
				el._title.style.transform = 'translateY(-100%) scale(' + k + ')';
				el._title.style.maxWidth = (rec.w * ctx.s) + 'px';
				el._title.style.fontSize = TITLE_PX + 'px';
			}
		}
	});
})(globalThis.MV = globalThis.MV || {});
