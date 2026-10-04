/* The top bar (back, title, search) and the bottom dock (frames, previous/next frame, zoom),
   which sits in the thumb zone on phones. */
(function (MV) {
	'use strict';

	var U = MV.util;
	var ui = MV.ui = MV.ui || {};

	ui.bar = {
		init: function (app) {
			var data = app.data, panels = ui.panels;
			var bits = [U.plural(data.items.length, 'item')];
			if (data.frames.length) bits.push(U.plural(data.frames.length, 'frame'));
			if (data.board.modifiedAt) bits.push('edited ' + U.date(String(data.board.modifiedAt).slice(0, 10)));
			U.$('subtitle').textContent = bits.join(' · ');

			U.$('searchBtn').addEventListener('click', function () {
				panels.toggle('searchPanel');
				if (panels.isOpen('searchPanel')) ui.search.focus();
			});
			function frames() { panels.toggle('framesPanel'); if (panels.isOpen('framesPanel')) ui.frames.reveal(); }
			U.$('framesBtn').addEventListener('click', frames);
			U.$('frameLabel').addEventListener('click', frames);
			U.$('prevFrame').addEventListener('click', function () { app.stepFrame(-1); });
			U.$('nextFrame').addEventListener('click', function () { app.stepFrame(1); });
			U.$('zoomLabel').addEventListener('click', function () { app.fitBoard(true); });
			U.$('zoomIn').addEventListener('click', function () { app.zoomBy(1.6); });
			U.$('zoomOut').addEventListener('click', function () { app.zoomBy(1 / 1.6); });

			if (!data.frames.length) document.body.classList.add('no-frames');
			if (!(data.unplaced || []).length && !Object.keys(data.skipped || {}).length) document.body.classList.add('no-extras');
		},

		update: function (cam) {
			var s = cam.s, text;
			if (s >= 10) text = Math.round(s) + '×';
			else if (s >= 0.1) text = Math.round(s * 100) + '%';
			else if (s >= 0.001) text = (s * 100).toFixed(1) + '%';
			else text = (s * 100).toFixed(2) + '%';
			var label = U.$('zoomLabel');
			if (label.textContent !== text) label.textContent = text;
		}
	};
})(globalThis.MV = globalThis.MV || {});
