/* Reopening a board returns to where you left it. The view is stored per board id in absolute
   board coordinates, so it stays put even if a later archive grows the board's bounds. */
(function (MV) {
	'use strict';

	var U = MV.util;
	var ui = MV.ui = MV.ui || {};

	function key(app) { return 'mv:view:' + app.data.board.id; }

	ui.viewstate = {
		/* {cx, cy, s}: the board point at the screen center, in the data's (rebased) coordinates. */
		load: function (app) {
			var v = U.storage.get(key(app), null), o = app.data.origin || [0, 0];
			if (!v || !isFinite(v.cx) || !isFinite(v.cy) || !(v.s > 0)) return null;
			return { cx: v.cx - o[0], cy: v.cy - o[1], s: v.s };
		},

		save: function (app, v) {
			var o = app.data.origin || [0, 0];
			U.storage.set(key(app), { cx: v.cx + o[0], cy: v.cy + o[1], s: v.s });
		}
	};
})(globalThis.MV = globalThis.MV || {});
