/* Mind map nodes: Miro's nodeColor is the node's fill, so the root is usually a colored pill and
   the rest sit on white. Branches between nodes are links (k: "m") drawn by link.js. */
(function (MV) {
	'use strict';

	var U = MV.util, R = MV.render;

	function dark(hex) {
		var m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || '');
		return !!m && (0.299 * parseInt(m[1], 16) + 0.587 * parseInt(m[2], 16) + 0.114 * parseInt(m[3], 16)) < 140;
	}

	R.register('mindmap_node', {
		create: function (rec) {
			var el = U.el('div', 'mv-mm' + (rec.root ? ' mv-mm-root' : ''));
			el.style.borderRadius = rec.h / 2 + 'px';
			var fill = rec.bg || rec.nc;
			if (fill) el.style.background = fill;
			var rt = R.richText(rec, el);
			if (dark(fill) && (!rec.fc || dark(rec.fc))) rt.style.color = '#fff';
			return el;
		},
		update: R.greek
	});
})(globalThis.MV = globalThis.MV || {});
