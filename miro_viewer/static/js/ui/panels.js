/* Panels: frames and search share one slot (a bottom sheet on phones, a sidebar on wide
   screens); the item sheet has its own. Escape closes the most recently opened one. */
(function (MV) {
	'use strict';

	var U = MV.util;
	var ui = MV.ui = MV.ui || {};
	var SLOT = { framesPanel: 'side', searchPanel: 'side', sheet: 'sheet' };
	var stack = [];
	var onClose = {};

	function wide() { return U.media('(min-width: 900px)'); }

	var panels = ui.panels = {
		isOpen: function (id) { return !U.$(id).hidden; },

		open: function (id) {
			Object.keys(SLOT).forEach(function (other) {
				/* Phones have room for one sheet; wide screens for one per slot. */
				if (other !== id && panels.isOpen(other) && (!wide() || SLOT[other] === SLOT[id])) panels.close(other);
			});
			var el = U.$(id);
			el.hidden = false;
			stack = stack.filter(function (x) { return x !== id; }).concat(id);
			document.body.classList.toggle('has-side', panels.isOpen('framesPanel') || panels.isOpen('searchPanel'));
		},

		close: function (id) {
			var el = U.$(id);
			if (el.hidden) return;
			el.hidden = true;
			stack = stack.filter(function (x) { return x !== id; });
			document.body.classList.toggle('has-side', panels.isOpen('framesPanel') || panels.isOpen('searchPanel'));
			if (onClose[id]) onClose[id]();
		},

		toggle: function (id) { if (panels.isOpen(id)) panels.close(id); else panels.open(id); },

		closeTop: function () {
			if (!stack.length) return false;
			panels.close(stack[stack.length - 1]);
			return true;
		},

		/* On a phone a sheet covers the bottom of the board: close it once a gesture starts. */
		closeTransient: function () {
			if (!wide()) Object.keys(SLOT).forEach(panels.close);
		},

		onClose: function (id, fn) { onClose[id] = fn; },

		init: function () {
			Object.keys(SLOT).forEach(function (id) {
				U.$(id).querySelectorAll('[data-close]').forEach(function (btn) {
					btn.addEventListener('click', function () { panels.close(id); });
				});
			});
		}
	};

	ui.toast = function (text) {
		var el = U.$('toast');
		el.textContent = text;
		el.hidden = false;
		clearTimeout(ui.toast._t);
		ui.toast._t = setTimeout(function () { el.hidden = true; }, 1800);
	};
})(globalThis.MV = globalThis.MV || {});
