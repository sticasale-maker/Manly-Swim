/* ocean-overlays.js: code-drawn arrows and labels over four painted illustrations on
   sea.html (8 Oct 2026). Same idea as the bay and wind-drift figures: the painting stays
   as it is, the words and arrows are drawn by code so they stay sharp and readable at any
   width. Physics only: these name what the painting shows, never what to do about it.

   A figure opts in with data-ov="surge|wall|cold|ssh" on its <figure class="ill-img">.
   The image is drawn with object-fit:cover, so its crop changes with the slot (16:9 open,
   2:1 on a phone card, a tall column beside the text on a wide closed card). Positions
   below are in the painting's own pixels (1280 x 714) and are mapped through that same
   crop; labels are kept inside the visible frame. Figures inside a chapter body that is
   built later (sea.html builds a chapter when it is first opened) are picked up on the
   'oceanchapter' event. ES5. */
(function () {
'use strict';
var IW = 1280, IH = 714;
var INK = '#0d2b3e', SUN = '#e0982f', CREAM = 'rgba(251,246,236,.94)', COLD = '#eaf6fb';

function setup(fig) {
  if (fig.__ov) return;
  var kind = fig.getAttribute('data-ov');
  if (!SPECS[kind]) return;
  var cv = document.createElement('canvas');
  cv.className = 'ov-cv';
  cv.setAttribute('aria-hidden', 'true');
  fig.appendChild(cv);
  fig.__ov = { cv: cv, kind: kind, w: 0, h: 0 };
  // a ResizeObserver reports the first size itself, so no separate first draw (one layout read, not two)
  if (window.ResizeObserver) new ResizeObserver(function () { draw(fig); }).observe(fig);
  else draw(fig);
}

function draw(fig) {
  var o = fig.__ov; if (!o) return;
  var w = fig.clientWidth, h = fig.clientHeight;
  if (!w || !h) return;                                  // folded away: drawn when it shows
  var dpr = Math.min(window.devicePixelRatio || 1, 3);
  o.cv.width = Math.round(w * dpr); o.cv.height = Math.round(h * dpr);
  var ctx = o.cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  // object-fit:cover, centred
  var s = Math.max(w / IW, h / IH), ox = (w - IW * s) / 2, oy = (h - IH * s) / 2;
  var g = {
    ctx: ctx, w: w, h: h, s: s,
    px: Math.max(10.5, Math.min(13, w / 30)),
    X: function (x) { return ox + x * s; }, Y: function (y) { return oy + y * s; },
    // the part of the painting in view, in painting pixels
    vx0: Math.max(0, -ox / s), vx1: Math.min(IW, (w - ox) / s),
    vy0: Math.max(0, -oy / s), vy1: Math.min(IH, (h - oy) / s)
  };
  SPECS[o.kind](g);
}

// A label: one or more lines, DM Mono on a cream halo, kept inside the frame.
function text(g, lines, x, y, align, col) {
  var c = g.ctx, lh = Math.round(g.px * 1.25), i, wmax = 0;
  if (typeof lines === 'string') lines = [lines];
  c.font = '500 ' + g.px + 'px "DM Mono", ui-monospace, monospace';
  for (i = 0; i < lines.length; i++) wmax = Math.max(wmax, c.measureText(lines[i]).width);
  var left = align === 'right' ? x - wmax : align === 'center' ? x - wmax / 2 : x;
  left = Math.max(6, Math.min(g.w - 6 - wmax, left));
  y = Math.max(g.px + 2, Math.min(g.h - 6 - lh * (lines.length - 1), y));
  c.textAlign = 'left'; c.textBaseline = 'alphabetic';
  c.lineJoin = 'round'; c.strokeStyle = CREAM; c.lineWidth = 3.5;
  for (i = 0; i < lines.length; i++) {
    var lx = align === 'right' ? left + wmax - c.measureText(lines[i]).width
      : align === 'center' ? left + (wmax - c.measureText(lines[i]).width) / 2 : left;
    c.strokeText(lines[i], lx, y + i * lh);
    c.fillStyle = col || INK; c.fillText(lines[i], lx, y + i * lh);
  }
  return { x: left, y: y - g.px, w: wmax, h: lh * lines.length };
}
// Too wide for one line at this size? Then the given break.
function fit(g, one, two) {
  g.ctx.font = '500 ' + g.px + 'px "DM Mono", ui-monospace, monospace';
  return g.ctx.measureText(one).width <= g.w - 16 ? [one] : two;
}
function arrow(g, tx, ty, hx, hy, col, wd, dash, head) {
  var c = g.ctx, hs = head || Math.max(7, Math.min(11, g.w / 45));
  var ux = hx - tx, uy = hy - ty, ul = Math.sqrt(ux * ux + uy * uy) || 1; ux /= ul; uy /= ul;
  c.save();
  c.lineCap = 'round'; c.lineJoin = 'round';
  // a soft cream edge under the arrow so it reads on any part of the painting
  c.strokeStyle = 'rgba(251,246,236,.75)'; c.lineWidth = wd + 3;
  if (dash) c.setLineDash(dash);
  c.beginPath(); c.moveTo(tx, ty); c.lineTo(hx - ux * hs, hy - uy * hs); c.stroke();
  c.strokeStyle = col; c.lineWidth = wd;
  c.beginPath(); c.moveTo(tx, ty); c.lineTo(hx - ux * hs, hy - uy * hs); c.stroke();
  c.setLineDash([]);
  c.beginPath(); c.moveTo(hx + ux * hs * 0.5, hy + uy * hs * 0.5);
  c.lineTo(hx - ux * hs - uy * hs * 0.75, hy - uy * hs + ux * hs * 0.75);
  c.lineTo(hx - ux * hs + uy * hs * 0.75, hy - uy * hs - ux * hs * 0.75); c.closePath();
  c.strokeStyle = 'rgba(251,246,236,.75)'; c.lineWidth = 2.5; c.stroke();
  c.fillStyle = col; c.fill();
  c.restore();
}
function leader(g, x1, y1, x2, y2) {
  var c = g.ctx;
  c.save(); c.lineCap = 'round';
  c.strokeStyle = 'rgba(251,246,236,.8)'; c.lineWidth = 3; c.beginPath(); c.moveTo(x1, y1); c.lineTo(x2, y2); c.stroke();
  c.strokeStyle = INK; c.lineWidth = 1.2; c.beginPath(); c.moveTo(x1, y1); c.lineTo(x2, y2); c.stroke();
  c.fillStyle = INK; c.beginPath(); c.arc(x2, y2, 2.4, 0, 2 * Math.PI); c.fill();
  c.restore();
}
function clampX(g, x, pad) { return Math.max(g.vx0 + pad, Math.min(g.vx1 - pad, x)); }

var SPECS = {
  // Surge, underwater: the kelp tips and the sand puff all lean right, so the water is
  // sliding right at this instant; half a period later it slides back.
  surge: function (g) {
    var y = g.Y(292), x0 = g.X(clampX(g, 300, 40)), x1 = g.X(clampX(g, 960, 40));
    arrow(g, x0, y, x1, y, SUN, Math.max(3, g.w / 160));
    var yb = y + Math.max(14, g.px * 1.6);
    arrow(g, x1 - (x1 - x0) * 0.18, yb, x0 + (x1 - x0) * 0.18, yb, INK, 1.6, [4, 4]);
    var L = fit(g, 'the water slides this way… then back', ['the water slides this way…', '…then back']);
    text(g, L, (x0 + x1) / 2, y - g.px * (L.length > 1 ? 2.2 : 1), 'center');
  },
  // The sea wall at high tide: the orange streak is the crest coming back off the wall.
  // A crest travels square to itself, so the arrow leaves the streak at a right angle,
  // away from the wall and out to sea, into the next crests coming in.
  wall: function (g) {
    var mx = 650, my = 518;                              // middle of the painted streak
    var hx = mx - 0.40 * 150, hy = my - 0.92 * 150;      // square to the streak, seaward
    arrow(g, g.X(mx), g.Y(my), g.X(hx), g.Y(hy), SUN, Math.max(2.5, g.w / 200));
    var L = fit(g, 'reflected wave going back out', ['reflected wave', 'going back out']);
    text(g, L, g.X(hx) - 6, g.Y(hy) - g.px * (L.length > 1 ? 1.6 : 0.4), 'right');
    // the criss-cross where the two sets of crests overlap
    var cx = g.X(705), cy = g.Y(598);
    var L2 = fit(g, 'waves bouncing back meet the next ones', ['waves bouncing back', 'meet the next ones']);
    var ly = g.Y(Math.min(g.vy1 - 18, 690)) - (L2.length - 1) * g.px * 1.25;
    var b = text(g, L2, g.X(clampX(g, 470, 30)), ly, 'center');
    leader(g, Math.min(b.x + b.w * 0.75, cx - 4), b.y - 3, cx, cy);
  },
  // Cold water, cross-section: land on the LEFT (west), sea on the RIGHT (east). The warm
  // top layer moves offshore and cold water comes up the slope to replace it. No wind is
  // drawn: which wind does this is the chapter's job, in words.
  cold: function (g) {
    var x0 = clampX(g, 770, 30), x1 = clampX(g, 1215, 30);
    var surf = function (x) { return 418 + (Math.max(610, x) - 610) * 0.142 / 2 + 2; }; // middle of the warm layer
    arrow(g, g.X(x0), g.Y(surf(x0)), g.X(x1), g.Y(surf(x1)), SUN, Math.max(2.5, g.w / 190));
    var L = fit(g, 'surface water drifts out to sea', ['surface water', 'drifts out to sea']);
    text(g, L, g.X(x1), g.Y(400) - (L.length - 1) * g.px * 1.25, 'right');
    // layers
    text(g, 'warm layer', g.X(clampX(g, 1120, 40)), g.Y(500), 'right', '#7a4a10');
    text(g, 'cold layer', g.X(clampX(g, 1180, 40)), g.Y(648), 'right', INK);
    // up the slope, inside the cold water, towards the shore
    var tx = clampX(g, 1094, 30), ty = 576, hx = clampX(g, 586, 20), hy = 480;   // in the water just above the seabed (was over the sand)
    arrow(g, g.X(tx), g.Y(ty), g.X(hx), g.Y(hy), COLD, Math.max(2.5, g.w / 190));
    // its label sits on the seabed below the slope, clear of the layer labels
    var L3 = fit(g, 'cold water rises to replace it', ['cold water rises', 'to replace it']);
    text(g, L3, g.X(clampX(g, 40, 10)), g.Y(Math.min(g.vy1 - 14, 682)) - (L3.length - 1) * g.px * 1.25, 'left');
  },
  // Sea level: the hill over the warm lens, and the satellite timing its radar echo.
  // No spin drawn (a cross-section cannot show it).
  ssh: function (g) {
    var hx = 905, hy = 366;
    var L = fit(g, 'warm water stands higher', ['warm water', 'stands higher']);
    var b = text(g, L, g.X(clampX(g, 770, 20)), g.Y(318) - (L.length - 1) * g.px * 1.25, 'right');
    leader(g, b.x + b.w + 4, b.y + b.h * 0.55, g.X(hx) - 2, g.Y(hy) - 3);
    // the echo: down and back up beside the beam
    var bx = g.X(934), top = g.Y(180), bot = g.Y(325);
    arrow(g, bx, top, bx, bot, INK, 1.2, null, 5);
    arrow(g, bx + 6, bot, bx + 6, top, INK, 1.2, null, 5);
    // beside the beam on the right if it fits there (on two lines if need be), else on its left
    g.ctx.font = '500 ' + g.px + 'px "DM Mono", ui-monospace, monospace';
    var room = g.w - 6 - (bx + 16), one = g.ctx.measureText('times a radar echo').width;
    var two = g.ctx.measureText('radar echo').width;
    if (room >= one) text(g, 'times a radar echo', bx + 16, g.Y(250), 'left');
    else if (room >= two - 12) text(g, ['times a', 'radar echo'], bx + 16, Math.max(g.Y(262), g.px * 4.6), 'left');
    else text(g, 'times a radar echo', bx - 8, g.Y(250), 'right');
  }
};

function scan(root) {
  [].forEach.call((root || document).querySelectorAll('figure[data-ov]'), setup);
}
function redrawAll() { [].forEach.call(document.querySelectorAll('figure[data-ov]'), draw); }
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { scan(); });
else scan();
document.addEventListener('oceanchapter', function () { scan(); });   // a new figure draws on its first ResizeObserver report
if (!window.ResizeObserver) window.addEventListener('resize', redrawAll);
if (document.fonts && document.fonts.status !== 'loaded' && document.fonts.ready) document.fonts.ready.then(redrawAll);
window.OceanOverlays = { scan: scan, redraw: redrawAll };
})();
