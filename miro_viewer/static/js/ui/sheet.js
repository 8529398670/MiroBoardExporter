/* The item sheet: what a tapped item is, its full text, who added it and when, and what can be
   done with it (view larger, open the file, open the link, open it in Miro). */
(function (MV) {
	'use strict';

	var U = MV.util;
	var ui = MV.ui = MV.ui || {};
	var app = null;

	function action(parent, label, opts) {
		var el;
		if (opts.href) {
			el = U.el('a', 'pill', parent, label);
			el.href = opts.href;
			if (opts.external) { el.target = '_blank'; el.rel = 'noopener noreferrer'; }
		} else {
			el = U.el('button', 'pill', parent, label);
			el.type = 'button';
			el.addEventListener('click', opts.onClick);
		}
		if (opts.primary) el.classList.add('primary');
		return el;
	}

	function miroUrl(id) {
		return 'https://miro.com/app/board/' + encodeURIComponent(app.data.board.id) + '/?moveToWidget=' + encodeURIComponent(id);
	}

	function title(rec) {
		if (rec.title) return rec.title;
		var text = U.plain(rec.html);
		if (text) return text.length > 80 ? text.slice(0, 78) + '…' : text;
		if (rec.t === 'document' && rec.pg) return 'Page ' + rec.pg;
		return MV.render.typeName(rec.t);
	}

	function typeLabel(rec) {
		var name = MV.render.typeName(rec.t);
		if (rec.t === 'shape' && rec.kind) name += ' · ' + rec.kind.replace(/_/g, ' ');
		if (rec.t === 'document') name = (rec.ext || 'document').toUpperCase() + ' ' + (rec.ext === 'pdf' ? 'page' : 'slide') + (rec.pg ? ' ' + (rec.pq ? '~' : '') + rec.pg : '');
		if (rec.t === 'embed' && rec.prov) name += ' · ' + rec.prov;
		if (rec.f) {
			var frame = app.scene.record(rec.f);
			if (frame && frame.title) name += ' · in “' + frame.title + '”';
		}
		return name;
	}

	ui.sheet = {
		init: function (a) {
			app = a;
			ui.panels.onClose('sheet', function () { app.deselect(); });
		},

		show: function (rec) {
			U.$('sheetType').textContent = typeLabel(rec);
			U.$('sheetTitle').textContent = title(rec);

			var body = U.$('sheetText');
			body.textContent = '';
			if (rec.html && U.plain(rec.html).length > 80) body.innerHTML = rec.html;
			body.hidden = !body.firstChild;

			var meta = [];
			var who = rec.cb && app.data.members[rec.cb];
			if (rec.ca) meta.push('Added ' + U.date(rec.ca) + (who ? ' by ' + who : ''));
			if (rec.ma) meta.push('edited ' + U.date(rec.ma));
			if (rec.m && rec.m.w) meta.push(rec.m.w + '×' + rec.m.h + ' px');
			U.$('sheetMeta').textContent = meta.join(' · ');

			var actions = U.$('sheetActions');
			actions.textContent = '';
			if (rec.m && MV.lod.sources(rec.m).length) {
				action(actions, 'View larger', { primary: true, onClick: function () { ui.lightbox.show(rec); } });
			}
			if (rec.t === 'document' && rec.o) {
				action(actions, 'Open ' + (rec.ext || 'file').toUpperCase(), { href: rec.o, external: true, primary: !rec.m });
			}
			if (rec.url) action(actions, 'Open link', { href: rec.url, external: true, primary: !rec.m });
			if (rec.t === 'frame') action(actions, 'Fit frame', { primary: true, onClick: function () { app.goFrame(app.data.frames.indexOf(rec.id), true); } });
			action(actions, 'Open in Miro', { href: miroUrl(rec.id), external: true });
			action(actions, 'Copy Miro link', {
				onClick: function () {
					U.copyText(miroUrl(rec.id)).then(function (ok) { ui.toast(ok ? 'Link copied' : 'Couldn’t copy the link'); });
				}
			});
			ui.panels.open('sheet');
		},

		hide: function () { ui.panels.close('sheet'); }
	};
})(globalThis.MV = globalThis.MV || {});
