/* Connectors and mind map branches. The scene gives each link a box from MV.geom.link; the SVG
   inside uses coordinates relative to that box, so the numbers stay small. */
(function (MV) {
	'use strict';

	var U = MV.util, R = MV.render;

	R.register('link', {
		create: function (rec) {
			var g = rec._g;
			var el = U.el('div', 'mv-link');
			var w = g.box.x2 - g.box.x1, h = g.box.y2 - g.box.y1;
			var svg = U.svg('svg', { width: w, height: h, viewBox: '0 0 ' + w + ' ' + h, 'aria-hidden': 'true' }, el);
			var sw = rec.w || 2;
			var line = { d: g.d, fill: 'none', stroke: rec.c, 'stroke-width': sw, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' };
			if (rec.d === 'dashed') line['stroke-dasharray'] = (sw * 4) + ' ' + (sw * 3);
			else if (rec.d === 'dotted') line['stroke-dasharray'] = '0 ' + (sw * 2);
			U.svg('path', line, svg);
			g.caps.forEach(function (cap) {
				U.svg('path', {
					d: cap.d, fill: cap.fill ? rec.c : 'none', stroke: rec.c,
					'stroke-width': cap.fill ? Math.min(sw, 2) : sw, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'
				}, svg);
			});
			(rec.cap || []).forEach(function (cap) {
				var p = g.at(cap.p);
				var label = R.richText({ html: cap.html, fs: rec.fs || 14, fc: rec.fc }, el, 'mv-cap');
				label.style.left = (p[0] - g.box.x1) + 'px';
				label.style.top = (p[1] - g.box.y1) + 'px';
			});
			return el;
		}
	});
})(globalThis.MV = globalThis.MV || {});
