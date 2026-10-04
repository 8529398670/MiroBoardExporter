/* The address fragment: #<item id> opens on that item, #v=<x>,<y>,<scale> on a view (center
   in board coordinates). A fragment never reaches a server and works from file://, and
   replaceState keeps the back button for leaving the board rather than every pan. */
(function (MV) {
	'use strict';

	var ui = MV.ui = MV.ui || {};

	function write(fragment) {
		try { history.replaceState(null, '', fragment ? '#' + fragment : location.pathname + location.search); }
		catch (e) { /* some local-file viewers refuse; the address is a convenience */ }
	}

	ui.hash = {
		parse: function () {
			var h = decodeURIComponent((location.hash || '').slice(1));
			var m = /^v=(-?[\d.]+),(-?[\d.]+),([\d.e-]+)$/.exec(h);
			if (m) return { view: { cx: +m[1], cy: +m[2], s: +m[3] } };
			if (/^[\w-]+$/.test(h)) return { item: h };
			return {};
		},

		item: function (id) { write(id); },

		view: function (v) {
			write('v=' + Math.round(v.cx) + ',' + Math.round(v.cy) + ',' + Number(v.s.toPrecision(4)));
		}
	};
})(globalThis.MV = globalThis.MV || {});
