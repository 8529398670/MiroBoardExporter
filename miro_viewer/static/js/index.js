/* The board list. boards.js calls MV.showBoards([...summaries]); the filter and the sort order
   are remembered on this device. */
(function (MV) {
	'use strict';

	var U = MV.util;
	var SORTS = {
		name: function (a, b) { return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }); },
		recent: function (a, b) { return String(b.modifiedAt || '').localeCompare(String(a.modifiedAt || '')); },
		items: function (a, b) { return b.items - a.items; }
	};

	function stats(b) {
		var bits = [U.plural(b.items, 'item')];
		if (b.frames) bits.push(U.plural(b.frames, 'frame'));
		if (b.images) bits.push(U.plural(b.images, 'image'));
		if (b.documents) bits.push(U.plural(b.documents, 'page'));
		return bits.join(' · ');
	}

	function card(b) {
		var li = U.el('li');
		var a = U.el('a', 'board', li);
		a.href = b.page;
		var cover = U.el('div', 'cover', a);
		if (b.cover) cover.style.backgroundImage = 'url("' + b.cover + '")';
		else cover.textContent = (b.name.trim()[0] || '?').toUpperCase();
		var text = U.el('div', 'board-text', a);
		U.el('p', 'board-name', text, b.name);
		U.el('p', 'board-stats', text, stats(b));
		if (b.modifiedAt) U.el('p', 'board-date', text, 'Edited ' + U.date(String(b.modifiedAt).slice(0, 10)));
		return li;
	}

	MV.showBoards = function (boards) {
		var list = U.$('boards'), filter = U.$('filter'), sort = U.$('sort');
		var total = boards.reduce(function (n, b) { return n + b.items; }, 0);
		U.$('summary').textContent = U.plural(boards.length, 'board') + ' · ' + U.plural(total, 'item');
		sort.value = U.storage.get('mv:sort', 'name');
		if (!SORTS[sort.value]) sort.value = 'name';
		filter.value = U.storage.get('mv:filter', '');

		function render() {
			var q = filter.value.trim().toLowerCase();
			var shown = boards.filter(function (b) { return !q || b.name.toLowerCase().indexOf(q) >= 0; }).sort(SORTS[sort.value]);
			list.textContent = '';
			shown.forEach(function (b) { list.appendChild(card(b)); });
			if (!shown.length) U.el('li', 'none', list, boards.length ? 'No board matches.' : 'No boards built yet.');
		}

		filter.addEventListener('input', function () { U.storage.set('mv:filter', filter.value); render(); });
		sort.addEventListener('change', function () { U.storage.set('mv:sort', sort.value); render(); });
		render();
	};
})(globalThis.MV = globalThis.MV || {});
