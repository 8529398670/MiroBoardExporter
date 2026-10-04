/* Shapes: an SVG outline per Miro shape kind, with the text box on top. Unknown kinds fall
   back to a rectangle. Paths are in the item's own w x h box. */
(function (MV) {
	'use strict';

	var U = MV.util, R = MV.render;

	function poly(points) { return 'M' + points.map(function (p) { return p[0] + ' ' + p[1]; }).join('L') + 'Z'; }

	function rect(w, h, r) {
		r = Math.min(r || 0, w / 2, h / 2);
		if (!r) return 'M0 0H' + w + 'V' + h + 'H0Z';
		return 'M' + r + ' 0H' + (w - r) + 'A' + r + ' ' + r + ' 0 0 1 ' + w + ' ' + r + 'V' + (h - r) +
			'A' + r + ' ' + r + ' 0 0 1 ' + (w - r) + ' ' + h + 'H' + r + 'A' + r + ' ' + r + ' 0 0 1 0 ' + (h - r) +
			'V' + r + 'A' + r + ' ' + r + ' 0 0 1 ' + r + ' 0Z';
	}

	function ellipse(w, h) {
		return 'M0 ' + h / 2 + 'A' + w / 2 + ' ' + h / 2 + ' 0 1 0 ' + w + ' ' + h / 2 + 'A' + w / 2 + ' ' + h / 2 + ' 0 1 0 0 ' + h / 2 + 'Z';
	}

	function star(w, h) {
		var pts = [];
		for (var i = 0; i < 10; i++) {
			var a = -Math.PI / 2 + i * Math.PI / 5, k = i % 2 ? 0.4 : 1;
			pts.push([w / 2 + Math.cos(a) * w / 2 * k, h / 2 + Math.sin(a) * h / 2 * k + h * 0.05]);
		}
		return poly(pts);
	}

	/* A curly brace bw wide and h tall at x0; `left` opens to the right like "{". */
	function brace(bw, h, left, x0) {
		x0 = x0 || 0;
		var q = Math.min(bw / 2, h * 0.08), m = x0 + bw / 2, e = left ? x0 + bw : x0, c = left ? x0 : x0 + bw;
		return 'M' + e + ' 0Q' + m + ' 0 ' + m + ' ' + q + 'L' + m + ' ' + (h / 2 - q) + 'Q' + m + ' ' + h / 2 + ' ' + c + ' ' + h / 2 +
			'Q' + m + ' ' + h / 2 + ' ' + m + ' ' + (h / 2 + q) + 'L' + m + ' ' + (h - q) + 'Q' + m + ' ' + h + ' ' + e + ' ' + h;
	}

	var PATHS = {
		rectangle: function (w, h) { return rect(w, h); },
		round_rectangle: function (w, h) { return rect(w, h, Math.min(w, h) * 0.12); },
		circle: ellipse,
		triangle: function (w, h) { return poly([[w / 2, 0], [w, h], [0, h]]); },
		rhombus: function (w, h) { return poly([[w / 2, 0], [w, h / 2], [w / 2, h], [0, h / 2]]); },
		parallelogram: function (w, h) { return poly([[w * 0.25, 0], [w, 0], [w * 0.75, h], [0, h]]); },
		trapezoid: function (w, h) { return poly([[w * 0.2, 0], [w * 0.8, 0], [w, h], [0, h]]); },
		pentagon: function (w, h) { return poly([[w / 2, 0], [w, h * 0.38], [w * 0.81, h], [w * 0.19, h], [0, h * 0.38]]); },
		hexagon: function (w, h) { return poly([[w * 0.25, 0], [w * 0.75, 0], [w, h / 2], [w * 0.75, h], [w * 0.25, h], [0, h / 2]]); },
		octagon: function (w, h) { var a = w * 0.29, b = h * 0.29; return poly([[a, 0], [w - a, 0], [w, b], [w, h - b], [w - a, h], [a, h], [0, h - b], [0, b]]); },
		star: star,
		cross: function (w, h) { var a = w / 3, b = h / 3; return poly([[a, 0], [2 * a, 0], [2 * a, b], [w, b], [w, 2 * b], [2 * a, 2 * b], [2 * a, h], [a, h], [a, 2 * b], [0, 2 * b], [0, b], [a, b]]); },
		right_arrow: function (w, h) { return poly([[0, h * 0.25], [w * 0.6, h * 0.25], [w * 0.6, 0], [w, h / 2], [w * 0.6, h], [w * 0.6, h * 0.75], [0, h * 0.75]]); },
		left_arrow: function (w, h) { return poly([[w, h * 0.25], [w * 0.4, h * 0.25], [w * 0.4, 0], [0, h / 2], [w * 0.4, h], [w * 0.4, h * 0.75], [w, h * 0.75]]); },
		left_right_arrow: function (w, h) { return poly([[0, h / 2], [w * 0.25, 0], [w * 0.25, h * 0.25], [w * 0.75, h * 0.25], [w * 0.75, 0], [w, h / 2], [w * 0.75, h], [w * 0.75, h * 0.75], [w * 0.25, h * 0.75], [w * 0.25, h]]); },
		can: function (w, h) {
			var e = Math.min(h * 0.12, w / 2);
			return 'M0 ' + e + 'V' + (h - e) + 'A' + w / 2 + ' ' + e + ' 0 0 0 ' + w + ' ' + (h - e) + 'V' + e +
				'A' + w / 2 + ' ' + e + ' 0 0 0 0 ' + e + 'A' + w / 2 + ' ' + e + ' 0 0 0 ' + w + ' ' + e;
		},
		wedge_round_rectangle_callout: function (w, h) {
			var b = h * 0.8, r = Math.min(w, b) * 0.12;
			return rect(w, b, r) + 'M' + w * 0.2 + ' ' + (b - 1) + 'L' + w * 0.15 + ' ' + h + 'L' + w * 0.35 + ' ' + (b - 1) + 'Z';
		},
		flow_chart_process: function (w, h) { return rect(w, h); },
		flow_chart_decision: function (w, h) { return PATHS.rhombus(w, h); },
		flow_chart_terminator: function (w, h) { return rect(w, h, h / 2); },
		flow_chart_data: function (w, h) { return PATHS.parallelogram(w, h); },
		flow_chart_input_output: function (w, h) { return PATHS.parallelogram(w, h); },
		flow_chart_preparation: function (w, h) { return PATHS.hexagon(w, h); },
		flow_chart_connector: ellipse,
		flow_chart_magnetic_disk: function (w, h) { return PATHS.can(w, h); },
		flow_chart_merge: function (w, h) { return poly([[0, 0], [w, 0], [w / 2, h]]); },
		flow_chart_manual_input: function (w, h) { return poly([[0, h * 0.25], [w, 0], [w, h], [0, h]]); },
		flow_chart_predefined_process: function (w, h) { var a = w * 0.1; return rect(w, h) + 'M' + a + ' 0V' + h + 'M' + (w - a) + ' 0V' + h; },
		flow_chart_document: function (w, h) { return 'M0 0H' + w + 'V' + h * 0.85 + 'C' + w * 0.75 + ' ' + h * 0.6 + ' ' + w * 0.25 + ' ' + h * 1.1 + ' 0 ' + h * 0.85 + 'Z'; },
		flow_chart_delay: function (w, h) { var r = Math.min(h / 2, w / 2); return 'M0 0H' + (w - r) + 'A' + r + ' ' + h / 2 + ' 0 0 1 ' + (w - r) + ' ' + h + 'H0Z'; },
		flow_chart_online_storage: function (w, h) {
			var a = Math.min(w * 0.15, h / 2);
			return 'M' + a + ' 0H' + w + 'A' + a + ' ' + h / 2 + ' 0 0 0 ' + w + ' ' + h + 'H' + a + 'A' + a + ' ' + h / 2 + ' 0 0 1 ' + a + ' 0Z';
		},
		flow_chart_note_curly_left: function (w, h) { return brace(Math.min(w * 0.2, h * 0.15), h, true, 0); },
		flow_chart_note_curly_right: function (w, h) { var bw = Math.min(w * 0.2, h * 0.15); return brace(bw, h, false, w - bw); },
		left_brace: function (w, h) { return brace(w, h, true); },
		right_brace: function (w, h) { return brace(w, h, false); }
	};
	var OPEN = { left_brace: 1, right_brace: 1, flow_chart_note_curly_left: 1, flow_chart_note_curly_right: 1 };

	R.shapePath = function (kind, w, h) { return (PATHS[kind] || PATHS.rectangle)(w, h); };

	R.register('shape', {
		create: function (rec) {
			var el = U.el('div', 'mv-shape');
			var svg = U.svg('svg', { width: rec.w, height: rec.h, viewBox: '0 0 ' + rec.w + ' ' + rec.h, 'aria-hidden': 'true' }, el);
			var open = OPEN[rec.kind];
			var attrs = {
				d: R.shapePath(rec.kind, rec.w, rec.h),
				fill: open ? 'none' : (rec.bg || 'none'),
				stroke: rec.bc || (open ? '#1a1a1a' : 'none'),
				'stroke-width': rec.bc ? rec.bw : (open ? 2 : 0),
				'stroke-linejoin': 'round'
			};
			if (rec.bs === 'dashed') attrs['stroke-dasharray'] = (rec.bw * 4) + ' ' + (rec.bw * 3);
			else if (rec.bs === 'dotted') { attrs['stroke-dasharray'] = '0 ' + (rec.bw * 2); attrs['stroke-linecap'] = 'round'; }
			U.svg('path', attrs, svg);
			if (rec.html) {
				R.textBox(rec, el, 0.08);
				MV.fitText.request(el, rec);
			}
			return el;
		},
		update: R.greek
	});
})(globalThis.MV = globalThis.MV || {});
