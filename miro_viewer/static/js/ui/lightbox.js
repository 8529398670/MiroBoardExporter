/* A picture on its own, as large as the screen allows: the 2048 px preview, or the original
   where that is small enough to be the best copy anyway. Tap anywhere to close. */
(function (MV) {
	'use strict';

	var U = MV.util;
	var ui = MV.ui = MV.ui || {};
	var app = null;

	function close() {
		var box = U.$('lightbox'), img = U.$('lightboxImg');
		box.hidden = true;
		img.onerror = null;
		img.removeAttribute('src');
	}

	ui.lightbox = {
		init: function (a) {
			app = a;
			U.$('lightbox').addEventListener('click', function (e) {
				if (!e.target.closest('a')) close();
			});
		},

		isOpen: function () { return !U.$('lightbox').hidden; },
		close: close,

		show: function (rec) {
			var m = rec.m, root = app.data.media;
			var sources = MV.lod.sources(m);
			var best = sources.length ? sources[sources.length - 1] : null;
			if (!best) return;
			var img = U.$('lightboxImg');
			img.style.backgroundColor = m.c || '';
			/* The canvas shows a GIF's first frame; here it plays, from the archive. If the archive
			   isn't next to the site (a copy of the site alone), the preview stays. */
			var preview = MV.render.pictureUrl(m, best.id, root);
			img.onerror = function () { img.onerror = null; img.src = preview; };
			img.src = m.a && m.o ? m.o : preview;
			var orig = U.$('lightboxOrig');
			var file = m.o || rec.o;
			orig.hidden = !file;
			if (file) orig.href = file;
			U.$('lightbox').hidden = false;
		}
	};
})(globalThis.MV = globalThis.MV || {});
