/* Miro shrinks or grows the text of sticky notes and many shapes to fill them. The builder
   estimates that size from character counts; here each such item is measured once, while the
   browser is idle, and the result is kept on the record (fa: 2) so it is never measured again. */
(function (MV) {
	'use strict';

	var F = MV.fitText = {};
	var queue = [];
	var scheduled = false;
	var idle = typeof requestIdleCallback === 'function'
		? function (fn) { requestIdleCallback(fn, { timeout: 400 }); }
		: function (fn) { setTimeout(function () { fn({ timeRemaining: function () { return 8; } }); }, 60); };

	F.request = function (el, rec) {
		if (rec.fa !== 1) return;
		queue.push({ el: el, rec: rec });
		if (!scheduled) { scheduled = true; idle(run); }
	};

	/* The wrapper's padding is part of its client size, so compare against the inner box. */
	function overflows(wrap, rt) {
		var cs = getComputedStyle(wrap);
		var innerH = wrap.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
		var innerW = wrap.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
		return rt.scrollHeight > innerH + 0.5 || rt.scrollWidth > innerW + 0.5;
	}

	function fit(job) {
		var el = job.el, rec = job.rec;
		if (!el.isConnected || el._greek || rec.fa !== 1) return;
		var rt = el.querySelector('.mv-rt'), wrap = rt && rt.parentNode;
		if (!rt || !wrap.clientHeight) return;
		/* Miro shrinks text until its longest word fits rather than breaking words, so measure
		   with breaking off: a word that doesn't fit then shows up as horizontal overflow. */
		rt.style.overflowWrap = 'normal';
		var lo = rec.fs * 0.4, hi = Math.min(rec.fs * 2.5, wrap.clientHeight);
		rt.style.fontSize = hi + 'px';
		if (overflows(wrap, rt)) {
			for (var i = 0; i < 7; i++) {
				var mid = (lo + hi) / 2;
				rt.style.fontSize = mid + 'px';
				if (overflows(wrap, rt)) hi = mid; else lo = mid;
			}
		} else {
			lo = hi;
		}
		/* A hair smaller: a word that fits to within a pixel would still break once wrapping is back on. */
		rec.fs = Math.round(lo * 0.96 * 10) / 10;
		rec.fa = 2;
		rt.style.fontSize = rec.fs + 'px';
		rt.style.overflowWrap = '';
	}

	function run(deadline) {
		scheduled = false;
		while (queue.length && deadline.timeRemaining() > 2) fit(queue.shift());
		if (queue.length) { scheduled = true; idle(run); }
	}
})(globalThis.MV = globalThis.MV || {});
