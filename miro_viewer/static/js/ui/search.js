/* Search the text of every item, frame title, document and link on the board. The index is
   built the first time the panel opens; picking a result flies to the item and rings it. */
(function (MV) {
	'use strict';

	var U = MV.util;
	var ui = MV.ui = MV.ui || {};
	var MAX_RESULTS = 80;
	var index = null, app = null, first = null;

	function build() {
		index = [];
		app.data.items.forEach(function (rec) {
			var text = [rec.title, U.plain(rec.html), rec.prov].filter(Boolean).join(' — ');
			if (text) index.push({ id: rec.id, t: rec.t, text: text, lower: text.toLowerCase() });
		});
	}

	function snippet(text, terms) {
		var lower = text.toLowerCase(), at = lower.indexOf(terms[0]);
		var start = Math.max(0, at - 30), cut = text.slice(start, start + 140);
		var html = U.escape((start ? '…' : '') + cut + (start + 140 < text.length ? '…' : ''));
		terms.forEach(function (t) {
			var safe = U.escape(t).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
			html = html.replace(new RegExp('(' + safe + ')', 'gi'), '<mark>$1</mark>');
		});
		return html;
	}

	function run(query) {
		if (!index) build();
		var list = U.$('searchResults'), count = U.$('searchCount');
		list.textContent = '';
		first = null;
		var terms = query.toLowerCase().split(/\s+/).filter(Boolean);
		if (!terms.length) { count.textContent = ''; return; }
		var hits = index.filter(function (e) { return terms.every(function (t) { return e.lower.indexOf(t) >= 0; }); });
		count.textContent = hits.length ? U.plural(hits.length, 'match') + (hits.length > MAX_RESULTS ? ', showing ' + MAX_RESULTS : '') : 'No matches';
		hits.slice(0, MAX_RESULTS).forEach(function (hit, i) {
			var li = U.el('li', '', list);
			var btn = U.el('button', 'row result', li);
			btn.type = 'button';
			U.el('span', 'eyebrow', btn, MV.render.typeName(hit.t));
			U.el('span', 'label', btn).innerHTML = snippet(hit.text, terms);
			btn.addEventListener('click', function () { pick(hit.id); });
			if (i === 0) first = hit.id;
		});
	}

	function pick(id) {
		ui.panels.closeTransient();
		app.focusItem(id, { flash: true, select: true });
	}

	ui.search = {
		init: function (a) {
			app = a;
			var input = U.$('searchInput');
			input.addEventListener('input', U.debounce(function () { run(input.value); }, 120));
			input.addEventListener('keydown', function (e) {
				if (e.key === 'Enter' && first) { e.preventDefault(); input.blur(); pick(first); }
				if (e.key === 'Escape') { input.blur(); ui.panels.close('searchPanel'); }
			});
		},
		focus: function () {
			var input = U.$('searchInput');
			setTimeout(function () { input.focus(); input.select(); }, 30);
		}
	};
})(globalThis.MV = globalThis.MV || {});
