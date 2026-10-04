/* Shared helpers. Every script in static/js is a classic script wrapped like this one: no
   top-level declarations (classic scripts share one global scope), no DOM work at load
   time, everything hung off the MV namespace. That keeps them loadable from file:// on a
   phone, where module scripts are blocked, and requirable from Node for the tests. */
(function (MV) {
	'use strict';

	var U = MV.util = {};

	U.clamp = function (v, lo, hi) { return Math.min(hi, Math.max(lo, v)); };

	U.$ = function (id) { return document.getElementById(id); };

	U.el = function (tag, cls, parent, text) {
		var el = document.createElement(tag);
		if (cls) el.className = cls;
		if (text != null) el.textContent = text;
		if (parent) parent.appendChild(el);
		return el;
	};

	U.svg = function (tag, attrs, parent) {
		var el = document.createElementNS('http://www.w3.org/2000/svg', tag);
		for (var k in attrs) if (attrs[k] != null) el.setAttribute(k, attrs[k]);
		if (parent) parent.appendChild(el);
		return el;
	};

	U.escape = function (text) {
		return String(text == null ? '' : text).replace(/[&<>"']/g, function (c) {
			return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
		});
	};

	/* Plain text of sanitized rich text. A <template> parses without loading anything. */
	var tpl = null;
	U.plain = function (html) {
		if (!html) return '';
		if (!tpl) tpl = document.createElement('template');
		tpl.innerHTML = String(html).replace(/<\/(p|li)>|<br\s*\/?>/gi, '$& ');
		return tpl.content.textContent.replace(/\s+/g, ' ').trim();
	};

	U.media = function (query) {
		return typeof matchMedia === 'function' && matchMedia(query).matches;
	};
	U.isTouch = function () { return U.media('(pointer: coarse)'); };
	U.reducedMotion = function () { return U.media('(prefers-reduced-motion: reduce)'); };

	/* localStorage can be missing, full, or throw (private mode, some local-HTML apps):
	   it only ever holds conveniences, so failures are silent. */
	U.storage = {
		get: function (key, fallback) {
			try {
				var raw = window.localStorage.getItem(key);
				return raw == null ? fallback : JSON.parse(raw);
			} catch (e) { return fallback; }
		},
		set: function (key, value) {
			try { window.localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* ignore */ }
		}
	};

	U.copyText = function (text) {
		function fallback() {
			var area = U.el('textarea', 'mv-offscreen', document.body);
			area.value = text;
			area.select();
			var ok = false;
			try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
			area.remove();
			return Promise.resolve(ok);
		}
		if (navigator.clipboard && window.isSecureContext) {
			return navigator.clipboard.writeText(text).then(function () { return true; }, fallback);
		}
		return fallback();
	};

	U.host = function (url) {
		var m = /^https?:\/\/([^/?#]+)/i.exec(url || '');
		return m ? m[1].replace(/^www\./, '') : '';
	};

	U.date = function (ymd) {
		if (!ymd) return '';
		var d = new Date(ymd + 'T00:00:00');
		if (isNaN(d)) return ymd;
		try { return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }); }
		catch (e) { return ymd; }
	};

	U.plural = function (n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); };

	/* Runs fn at most once per animation frame. */
	U.rafThrottle = function (fn) {
		var queued = false;
		return function () {
			if (queued) return;
			queued = true;
			requestAnimationFrame(function () { queued = false; fn(); });
		};
	};

	U.debounce = function (fn, ms) {
		var t = null;
		return function () {
			var args = arguments, self = this;
			clearTimeout(t);
			t = setTimeout(function () { fn.apply(self, args); }, ms);
		};
	};
})(globalThis.MV = globalThis.MV || {});
