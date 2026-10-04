/* Renderer registry. One renderer per item type (or family), registered from its own file:

     MV.render.register('sticky_note', {
       create: function (rec, ctx) { return element; },   // world-sized element for the record
       update: function (el, rec, ctx) {},                 // optional: per view change (level of detail)
       place:  function (el, rec, ctx) {},                 // optional: position itself instead of the scene
       media:  true                                        // has a picture the scene picks files for
     });

   The scene positions every element at its world box; renderers only fill it in. Types without a
   renderer get the placeholder. */
(function (MV) {
	'use strict';

	var U = MV.util;
	var R = MV.render = { types: {} };

	R.register = function (types, renderer) {
		(Array.isArray(types) ? types : [types]).forEach(function (t) { R.types[t] = renderer; });
	};

	R.get = function (type) { return R.types[type] || R.types.placeholder; };

	var ALIGN = { l: 'left', c: 'center', r: 'right' };
	var VALIGN = { t: 'flex-start', m: 'center', b: 'flex-end' };

	/* The rich-text block shared by notes, shapes, text and mind map nodes. */
	R.richText = function (rec, parent, cls) {
		var box = U.el('div', 'mv-rt' + (cls ? ' ' + cls : ''), parent);
		box.innerHTML = rec.html || '';
		box.style.fontSize = (rec.fs || 14) + 'px';
		if (rec.ff) box.classList.add('ff-' + rec.ff);
		if (rec.fc) box.style.color = rec.fc;
		if (rec.al) box.style.textAlign = ALIGN[rec.al] || 'left';
		return box;
	};

	/* A flex wrapper that places rich text vertically (va) with padding as a share of the box. */
	R.textBox = function (rec, el, padShare) {
		var wrap = U.el('div', 'mv-tb', el);
		var pad = Math.min(rec.w, rec.h) * (padShare || 0);
		if (pad) wrap.style.padding = pad + 'px';
		wrap.style.justifyContent = VALIGN[rec.va] || 'center';
		return R.richText(rec, wrap);
	};

	/* Text drawn below a few screen pixels is unreadable and costs layout: show bars instead. */
	R.GREEK_PX = 3.5;
	R.greek = function (el, rec, ctx) {
		if (!rec.html) return;
		var tiny = (rec.fs || 14) * ctx.s < R.GREEK_PX;
		if (tiny !== el._greek) {
			el._greek = tiny;
			el.classList.toggle('greek', tiny);
			/* Auto-fitted text is measured once it is drawn readable. */
			if (!tiny && rec.fa === 1) MV.fitText.request(el, rec);
		}
	};
})(globalThis.MV = globalThis.MV || {});
