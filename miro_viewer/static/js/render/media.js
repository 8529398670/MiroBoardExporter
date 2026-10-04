/* Pictures: images, rendered document pages and embed previews.

   The scene decides which file each picture shows (see lod.js) and calls setPicture. A new file
   loads on top of the old one, which stays until the new one has decoded, so changing tiers
   never flashes. Below a few dozen pixels a picture is just its average color. */
(function (MV) {
	'use strict';

	var U = MV.util, R = MV.render;

	/* Previews live under media/ (root); the original (o) is a path into the archive. */
	R.pictureUrl = function (m, id, root) {
		if (id === 'o') return m.o;
		return root + m.k + (id === 'v' ? '.svg' : '.' + id + '.webp');
	};

	function drop(img) {
		if (!img) return;
		img.onload = img.onerror = null;
		img.removeAttribute('src');
		img.remove();
	}

	/* Show source `src` ({id, px} from MV.lod, or null for the color box) of media record m. */
	R.setPicture = function (el, m, src, root) {
		var id = src ? src.id : null;
		if (el._srcId === id) return;
		el._srcId = id;
		drop(el._pending);
		el._pending = null;
		if (id == null) {
			drop(el._img);
			el._img = null;
			return;
		}
		var img = new Image();
		img.decoding = 'async';
		img.draggable = false;
		img.alt = '';
		img.className = 'mv-pic';
		img.onload = function () {
			if (el._pending !== img) return;
			el._pending = null;
			var old = el._img;
			el._img = img;
			img.classList.add('ready');
			/* The old file stays under the new one until the new one has faded in. */
			if (old) setTimeout(function () { if (el._img !== old) drop(old); }, 160);
		};
		img.onerror = function () { if (el._pending === img) { el._pending = null; drop(img); } };
		el._pending = img;
		img.src = R.pictureUrl(m, id, root);
		el.appendChild(img);
	};

	R.releasePicture = function (el) {
		drop(el._pending);
		drop(el._img);
		el._pending = el._img = null;
		el._srcId = undefined;
	};

	function picture(rec, cls) {
		var el = U.el('div', 'mv-pic-box ' + cls);
		if (rec.m && rec.m.c) el.style.backgroundColor = rec.m.c;
		el._media = rec.m;
		return el;
	}

	R.register('image', {
		media: true,
		create: function (rec) {
			if (!rec.m) {
				var miss = U.el('div', 'mv-missing');
				U.el('span', '', miss, 'Image unavailable');
				return miss;
			}
			return picture(rec, 'mv-img');
		},
		release: R.releasePicture
	});

	function docCard(rec, el) {
		var card = U.el('div', 'mv-doc-card', el);
		card.style.fontSize = Math.max(8, Math.min(rec.w, rec.h) * 0.07) + 'px';
		U.el('div', 'mv-doc-ext', card, (rec.ext || 'file').toUpperCase());
		U.el('div', 'mv-doc-title', card, rec.title || 'Document');
		if (rec.pg) U.el('div', 'mv-doc-page', card, (rec.ext === 'pdf' ? 'Page ' : 'Slide ') + (rec.pq ? '~' : '') + rec.pg);
		return card;
	}

	R.register('document', {
		media: true,
		create: function (rec) {
			if (rec.m) return picture(rec, 'mv-img mv-page');
			var el = U.el('div', 'mv-doc');
			docCard(rec, el);
			return el;
		},
		release: R.releasePicture
	});

	R.register('embed', {
		media: true,
		create: function (rec) {
			var el = rec.m ? picture(rec, 'mv-embed') : U.el('div', 'mv-embed mv-embed-card');
			var bar = U.el('div', 'mv-embed-bar', el);
			bar.style.fontSize = Math.max(8, Math.min(rec.h * 0.08, rec.w * 0.04)) + 'px';
			U.el('span', 'mv-embed-title', bar, rec.title || U.host(rec.url) || 'Link');
			if (rec.prov || rec.url) U.el('span', 'mv-embed-prov', bar, rec.prov || U.host(rec.url));
			if (rec.ct === 'video') {
				var play = U.el('div', 'mv-play', el);
				play.style.width = play.style.height = Math.min(rec.w, rec.h) * 0.22 + 'px';
			}
			return el;
		},
		release: R.releasePicture
	});
})(globalThis.MV = globalThis.MV || {});
