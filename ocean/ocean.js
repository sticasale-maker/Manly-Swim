/* ocean.js: shared code for "How the sea works" (hub + chapters). ES5, no build step.
   PROTOTYPE 7 Oct 2026. In production: /Manly-Swim/ocean/ocean.js, loaded by every page
   as ocean.js?v=<first 8 hex of this file's sha1>, BEFORE the page's own figure modules.

   The first part is waves.html's shared code, copied verbatim (commit ef791a0): the
   physics helpers, runCanvas, and the ONE baked copy of the bay model (MODEL, SS, FETCH,
   chop and sea-wall knobs, SAVED_ON). CLAUDE.md section 7 applies to this file: the
   baked values equal the app's SITE defaults; if a SITE default changes in index.html
   (not via /tune), change it here, once, and every chapter follows.
   One change from waves.html: liveSettings() no longer runs on load. A chapter that
   needs the live shelter table / chop knobs (wind, simulator) calls OCEAN.live(), so
   the other chapters never touch the sheet proxy or the Worker.
   Everything is exported on window.OCEAN (see the end). */
(function () {
'use strict';
var G = 9.81;
var REDUCED = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// ── MODEL (see the header comment for provenance) ────────────────────────────
var MODEL = {
  // bay-mouth swell = f(dir)·(T/10)^q·H^r on the hindcast's own basis. f is piecewise-linear
  // on these knots and held flat beyond the ends (30 Sep 2026, owner's yes: it replaced a
  // Gaussian whose peak was extrapolated towards the NE and over-read ENE swell).
  dirKnots: [60, 75, 90, 105, 120, 135, 150, 165, 180],
  dirF:     [0.831, 1.089, 0.861, 0.679, 0.602, 0.443, 0.272, 0.229, 0.272],
  q: 0.679, r: 1.084,
  // forecast (WillyWeather) -> hindcast basis, from 1,416 matched hours
  wwH: 1.30, wwT: 1.16, wwDir: 7,
  // forecast period -> bay-mouth peak period (NS peak / hindcast mean 1.27; WW/hindcast 1.16)
  mouthT: 1.27 / 1.16,
  // measured all-period medians, already on the forecast basis: [dir, share]
  measured: [[67,.47],[82,.63],[97,.66],[112,.46],[127,.41],[142,.29],[157,.20],[172,.16]],
  // forecast direction -> median arrival direction at the bay mouth. The ENE row is 72, not
  // the raw 85 (owner's yes, 29 Sep 2026): at NE the Nearshore "swell" is often a second,
  // easterly train, because it files anything under 8.2 s as sea. When it is the SAME train
  // it arrives from ~71-79 deg; on 216 real WW NE hours the error falls from 12.7 to 9.3 deg.
  bend: [[67,72],[82,81.5],[97,85.4],[112,99.2],[127,111.9],[142,118.9],[157,124.1],[172,124.6],[187,124.7]],
  nodeDepth: 10   // m, roughly the bay mouth where node 103218 sits
};
// The app's South Steyne shelter table (New NS sheet, SS block), folded 0–180° @10°,
// periods 2–17 s. Transmit % of wave ENERGY from the bay mouth to the entry corner.
var SS = [[100,100,100,100,100,100,100,100,100,100,100,100,100,100,100,100],[99,99,99,99,99,99,99,99,100,100,100,100,100,100,100,100],[99,99,99,99,99,99,99,99,100,100,100,100,100,100,100,100],[98,98,98,98,98,99,99,99,99,99,99,99,100,100,100,100],[93,93,94,94,95,95,96,96,97,97,98,98,98,99,99,100],[84,84,85,86,87,88,89,90,91,92,93,94,94,95,96,97],[74,74,75,77,78,79,80,82,83,84,85,86,88,89,90,91],[64,64,66,68,69,71,72,74,75,77,79,80,82,83,85,87],[54,54,56,58,59,61,63,65,66,68,70,71,73,75,77,78],[45,45,47,48,50,52,53,55,57,58,60,61,63,65,66,68],[36,36,38,39,40,41,42,44,45,46,47,49,50,51,52,53],[27,27,28,29,30,31,32,32,33,34,35,36,37,37,38,39],[21,21,21,22,22,22,23,23,24,24,25,25,26,26,27,27],[14,14,15,15,15,16,16,16,17,17,17,18,18,18,19,19],[8.9,8.9,9,9,9.1,9.2,9.3,9.3,9.4,9.5,9.5,9.6,9.7,9.8,9.8,9.9],[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]];
// The app's chop fetch table: metres of water, index = wind-from bearing / 10.
var FETCH = [1766,2766,5936,5890,5456,4445,3599,2572,1622,800,257,232,171,127,109,94,72,57,50,46,46,47,52,60,70,74,87,115,159,214,412,624,789,1022,1273,1530];
var CHOP_GAIN = 0.60, LEE = 0.75, LEE_FROM = 180, LEE_TO = 280, LEE_TAPER = 20;
// Sea wall: the app's tide window (surgeWallTideLo/Hi, m) — water starts reaching the wall at
// WALL_LO and reflects fully by WALL_HI. WALL_R is the PHYSICAL reflection coefficient of a
// vertical wall (~0.9), not the app's scoring share.
var WALL_LO = 1.4, WALL_HI = 1.8, WALL_R = 0.9;
var SAVED_ON = '29 Sep 2026';                               // date of the baked copies above

// ── Live settings: the same two sources the app reads ─────────────────────────
// The SS shelter table from the New NS sheet (via the sheet proxy), and the chop and wall
// knobs from the Worker's /tune store (which only carries what has been pushed live; the
// rest stay at the baked app defaults). Both allow any origin. On success the values are
// swapped in, the note under the simulator says so with the time, and 'wavesconfig' fires
// so the simulator and the wind figure rebuild. On failure the dated baked copies stand.
function liveSettings() {
  var SHEET = 'https://middleman-to-sheet.sticasale.workers.dev/newindex/nsdisp';
  var TUNE  = 'https://bold-rain-6ded.sticasale.workers.dev/tune';
  var got = { sheet: false, tune: false }, pending = 2;
  function timed(url) {
    var ctl = ('AbortController' in window) ? new AbortController() : null;
    var t = setTimeout(function () { if (ctl) ctl.abort(); }, 8000);
    return fetch(url + '?t=' + Date.now(), ctl ? { signal: ctl.signal } : {})
      .then(function (r) { clearTimeout(t); if (!r.ok) throw new Error('HTTP ' + r.status); return r; });
  }
  function csvRows(txt) {                                  // small CSV reader: quotes, commas, CRLF
    var rows = [], row = [], f = '', q = false;
    for (var i = 0; i < txt.length; i++) {
      var ch = txt[i];
      if (q) { if (ch === '"') { if (txt[i + 1] === '"') { f += '"'; i++; } else q = false; } else f += ch; }
      else if (ch === '"') q = true;
      else if (ch === ',') { row.push(f); f = ''; }
      else if (ch === '\n' || ch === '\r') { if (ch === '\r' && txt[i + 1] === '\n') i++; row.push(f); rows.push(row); row = []; f = ''; }
      else f += ch;
    }
    if (f !== '' || row.length) { row.push(f); rows.push(row); }
    return rows;
  }
  // Same cells the app reads (parseNsDispCsv): CSV rows 10–28 = bearings 0–180 deg, SS block
  // columns 3–18 = periods 2–17 s; blank = 0 (blocked). The app pins SS to this folded half.
  function parseSS(txt) {
    var rows = csvRows(txt), t = [];
    for (var r = 10; r <= 28; r++) {
      var row = rows[r] || [], out = [];
      for (var c = 3; c <= 18; c++) { var v = parseFloat(row[c]); out.push(isFinite(v) ? v : 0); }
      t.push(out);
    }
    var ok = t[0][8] >= 50 && t.every(function (r) { return r.every(function (v) { return v >= 0 && v <= 100; }); });
    return ok ? t : null;
  }
  function num(v, lo, hi) { return (typeof v === 'number' && isFinite(v) && v >= lo && v <= hi) ? v : null; }
  function finish() {
    if (--pending) return;
    var el = document.getElementById('simSrc');
    if (el) {
      var hhmm = new Date().toLocaleTimeString('en-AU', { hour: 'numeric', minute: '2-digit' });
      el.textContent = (got.sheet && got.tune)
        ? 'Shelter table and chop settings: live from the app\u2019s settings, loaded at ' + hhmm + '.'
        : (got.sheet || got.tune)
          ? 'Shelter table: ' + (got.sheet ? 'live' : 'the copy saved on ' + SAVED_ON) + '. Chop and sea-wall settings: ' +
            (got.tune ? 'live' : 'the copy saved on ' + SAVED_ON) + ' (checked at ' + hhmm + ').'
          : 'Shelter table and chop settings: the copy saved on ' + SAVED_ON + ' (the live settings could not be reached).';
    }
    if (got.sheet || got.tune) { try { document.dispatchEvent(new Event('wavesconfig')); } catch (e) {} }
  }
  timed(SHEET).then(function (r) { return r.text(); }).then(function (txt) {
    var t = parseSS(txt); if (t) { SS = t; got.sheet = true; }
  }).catch(function () {}).then(finish);
  timed(TUNE).then(function (r) { return r.json(); }).then(function (j) {
    var v = j && j.values; if (!v) return;
    var x;
    if ((x = num(v.chopWindGain, 0.05, 3)) != null) CHOP_GAIN = x;
    if ((x = num(v.chopLeeFactor, 0.05, 1)) != null) LEE = x;
    if ((x = num(v.chopLeeFrom, 0, 360)) != null) LEE_FROM = x;
    if ((x = num(v.chopLeeTo, 0, 360)) != null) LEE_TO = x;
    if ((x = num(v.chopLeeTaper, 0, 90)) != null) LEE_TAPER = x;
    if ((x = num(v.surgeWallTideLo, 0, 3)) != null) WALL_LO = x;
    if ((x = num(v.surgeWallTideHi, 0, 3)) != null) WALL_HI = x;
    if (!(WALL_HI > WALL_LO)) WALL_HI = WALL_LO + 0.4;
    got.tune = true;
  }).catch(function () {}).then(finish);
}

function ang(a, b) { return ((a - b + 540) % 360) - 180; }
function interp(tbl, x) {
  if (x <= tbl[0][0]) return tbl[0][1];
  for (var i = 1; i < tbl.length; i++) if (x <= tbl[i][0]) {
    var f = (x - tbl[i-1][0]) / (tbl[i][0] - tbl[i-1][0]);
    return tbl[i-1][1] + f * (tbl[i][1] - tbl[i-1][1]);
  }
  return tbl[tbl.length-1][1];
}
function compass(d) {
  var n = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];
  return n[Math.round((((d % 360) + 360) % 360) / 22.5) % 16];
}
// bay-mouth swell height from a FORECAST-basis swell
function dirShare(Do) {                                // piecewise-linear, flat beyond the ends
  var k = MODEL.dirKnots, f = MODEL.dirF, n = k.length;
  if (Do <= k[0]) return f[0];
  if (Do >= k[n - 1]) return f[n - 1];
  for (var i = 1; i < n; i++) if (Do <= k[i]) return f[i - 1] + (f[i] - f[i - 1]) * (Do - k[i - 1]) / (k[i] - k[i - 1]);
  return f[n - 1];
}
function mouthH(H, T, D) {
  var Ho = H / MODEL.wwH, To = T / MODEL.wwT, Do = D - MODEL.wwDir;
  return dirShare(Do) * Math.pow(To / 10, MODEL.q) * Math.pow(Ho, MODEL.r);
}
function ssTransmit(dir, T) {                         // same bilinear + fold as the app
  var d = ((dir % 360) + 360) % 360; if (d > 180) d = 360 - d;
  var per = Math.max(2, Math.min(17, T));
  var ri = d / 10, r0 = Math.floor(ri), r1 = Math.min(18, r0 + 1), rf = ri - r0;
  var ci = per - 2, c0 = Math.floor(ci), c1 = Math.min(15, c0 + 1), cf = ci - c0;
  var tx = SS[r0][c0]*(1-rf)*(1-cf) + SS[r0][c1]*(1-rf)*cf + SS[r1][c0]*rf*(1-cf) + SS[r1][c1]*rf*cf;
  return Math.max(0, tx / 100);
}
// wavenumber from the dispersion relation w^2 = g k tanh(k h)
function waveK(T, h) {
  var w = 2 * Math.PI / T, k = w * w / G;
  for (var i = 0; i < 40; i++) {
    var th = Math.tanh(k * h), f = G * k * th - w * w;
    var df = G * th + G * k * h * (1 - th * th);
    k -= f / df;
  }
  return k;
}
function groupSpeed(T, h) {
  var k = waveK(T, h), c = (2 * Math.PI / T) / k, kh = k * h;
  return c * 0.5 * (1 + 2 * kh / Math.sinh(2 * kh));
}
function spmGrowth(U, F) {                            // the app's fetch-limited growth law
  if (U <= 0 || F <= 0) return 0;
  var X = G * F / (U * U);
  return Math.min(1.6e-3 * Math.sqrt(X) * U * U / G, 0.243 * U * U / G);
}
function leeFactor(d) {
  d = ((d % 360) + 360) % 360;
  var out = (d >= LEE_FROM && d <= LEE_TO) ? 0 : Math.min(Math.abs(ang(d, LEE_FROM)), Math.abs(ang(d, LEE_TO)));
  if (out >= LEE_TAPER) return 1;
  return 1 - (1 - LEE) * Math.cos((out / LEE_TAPER) * Math.PI / 2);
}
function fetchFor(d) {
  d = ((d % 360) + 360) % 360;
  var i = Math.floor(d / 10), f = (d - i * 10) / 10;
  return FETCH[i] + (FETCH[(i + 1) % 36] - FETCH[i]) * f;
}

// ── Shared canvas runner (every animation below uses it) ────────────────────
// Sizes a canvas for the device pixel ratio at a fixed CSS height, runs
// draw(ctx, W, H, t) every frame while the canvas is on screen (t in seconds), pauses
// off screen, and under prefers-reduced-motion draws one still frame (t = 0).
// Returns { redraw } — call it after a control changes so a paused or still canvas
// shows the new setting.
function runCanvas(cv, height, draw) {
  var ctx = cv.getContext('2d'), visible = false, raf = 0, t0 = performance.now(), W = 0;
  function size() {
    var dpr = window.devicePixelRatio || 1;
    W = cv.clientWidth || 300;
    cv.style.height = height + 'px';
    cv.width = Math.round(W * dpr); cv.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function now() { return REDUCED ? 0 : (performance.now() - t0) / 1000; }
  function frame() { draw(ctx, W, height, now()); if (!REDUCED && visible) raf = requestAnimationFrame(frame); }
  function redraw() { if (REDUCED || !visible) draw(ctx, W, height, now()); }
  size();
  window.addEventListener('resize', function () { size(); redraw(); });
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (es) {
      var v = es[0].isIntersecting; if (v === visible) return;
      visible = v; cancelAnimationFrame(raf);
      if (visible && !REDUCED) raf = requestAnimationFrame(frame); else redraw();
    }).observe(cv);
  } else { visible = true; if (!REDUCED) raf = requestAnimationFrame(frame); }
  draw(ctx, W, height, 0);
  // A still frame drawn before DM Mono arrives would keep the fallback font for good
  // (reduced motion, or a figure still off screen), so draw again once the fonts land.
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { redraw(); });
  return { redraw: redraw };
}

// ══════════════════════════════════════════════════════════════════════════════
// NEW BELOW THIS LINE (prototype 7 Oct 2026). Everything above is waves.html's
// shared code, copied verbatim, except that liveSettings no longer runs on its own:
// a page that needs the live shelter table and chop knobs calls OCEAN.live().
// ══════════════════════════════════════════════════════════════════════════════

var liveStarted = false;
function live() { if (liveStarted) return; liveStarted = true; liveSettings(); }

// ── Decorative swell line under a hero (the body of waves.html's "hero" module) ──
function heroWave(cv) {
  if (!cv) return;
  var LAYERS = [                                       // L, a in CSS px; y = still-water line
    { L: 190, a: 2.8, y: 13, col: 'rgba(127,188,210,.8)', w: 1.2, ph: 0.0 },
    { L: 124, a: 3.2, y: 21, col: 'rgba(10,126,164,.55)', w: 1.3, ph: 1.9 },
    { L: 82,  a: 3.4, y: 30, col: 'rgba(11,79,108,.78)', w: 1.6, ph: 4.1, fill: true }
  ];
  var V0 = 13;                                         // CSS px/s for the longest train
  var pts = [];
  runCanvas(cv, 44, function (ctx, W, H, t) {
    ctx.clearRect(0, 0, W, H);
    for (var i = 0; i < LAYERS.length; i++) {
      var s = LAYERS[i], k = 2 * Math.PI / s.L, w = k * V0 * Math.sqrt(s.L / LAYERS[0].L), ka = k * s.a, n = 0;
      for (var x = 0; x <= W + 3; x += 3) {
        var th = k * x - w * t + s.ph;                 // crests travel towards +x
        pts[n++] = x; pts[n++] = s.y - s.a * (Math.cos(th) + 0.5 * ka * Math.cos(2 * th));
      }
      if (s.fill) {
        var g = ctx.createLinearGradient(0, s.y - s.a, 0, H);
        g.addColorStop(0, 'rgba(127,188,210,.38)'); g.addColorStop(1, 'rgba(127,188,210,0)');
        ctx.beginPath(); ctx.moveTo(0, H);
        for (var p = 0; p < n; p += 2) ctx.lineTo(pts[p], pts[p + 1]);
        ctx.lineTo(pts[n - 2], H); ctx.closePath(); ctx.fillStyle = g; ctx.fill();
      }
      ctx.beginPath(); ctx.moveTo(pts[0], pts[1]);
      for (var q = 2; q < n; q += 2) ctx.lineTo(pts[q], pts[q + 1]);
      ctx.strokeStyle = s.col; ctx.lineWidth = s.w; ctx.lineJoin = 'round'; ctx.stroke();
    }
    // soft ends: fade both sides to nothing
    ctx.globalCompositeOperation = 'destination-in';
    var m = ctx.createLinearGradient(0, 0, W, 0);
    m.addColorStop(0, 'rgba(0,0,0,0)'); m.addColorStop(0.16, '#000'); m.addColorStop(0.84, '#000'); m.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = m; ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = 'source-over';
  });
}

// ── The chapter list. ONE list drives "chapter X of N", previous / next, the
// sticky bar's number and the hub's read ticks. The hub's cards are static HTML (so
// they read without script and unfurl for crawlers); hub() warns in the console if
// their order drifts from this list.
// PROTOTYPE hrefs: chapters still inside waves.html point at its live anchors, new
// chapters at the scratch prototypes. In production every href is slug + '.html'.
var HUB = 'hub.html';                                  // production: './'
var APP = 'https://app.viz.net.au/Manly-Swim/';        // production: '../'
var LIVE = 'https://app.viz.net.au/Manly-Swim/waves.html';
var THEMES = {
  waves: 'Waves', wind: 'Wind', sand: 'Currents & sand', tides: 'Tides & sea level',
  temp: 'Temperature & colour', tools: 'Tools'
};
var CHAPTERS = [
  { slug: 'waves',          theme: 'waves', min: 4, href: 'chapter-template.html',
    title: 'What a wave is',                 hook: 'When a swell lifts you, why do you end up back where you started?' },
  { slug: 'swell-and-sets', theme: 'waves', min: 4, href: LIVE + '#swell-sea',
    title: 'Swell, sea and sets',            hook: 'Why do the bigger waves come in groups, with flat spells in between?' },
  { slug: 'into-the-bay',   theme: 'waves', min: 5, href: LIVE + '#shoaling',
    title: 'How swell gets into the bay',    hook: 'Why can a two-metre south-easterly barely reach the corner, while a one-metre easterly runs straight in?' },
  { slug: 'point-and-wall', theme: 'waves', min: 4, href: LIVE + '#point',
    title: 'The point and the sea wall',     hook: 'Why does the water slosh harder by the point, and off the sea wall at high tide?' },
  { slug: 'wind',           theme: 'wind',  min: 4, href: 'fetch.html',
    title: 'Wind and chop',                  hook: 'Why is it rough today when the forecast said the swell was small?' },
  { slug: 'currents',       theme: 'sand',  min: 3, href: LIVE + '#drift',
    title: 'Carried along',                  hook: 'Why do you come out further along the beach than where you went in?' },
  { slug: 'sand',           theme: 'sand',  min: 5, href: 'sand.html',
    title: 'Where the sand goes',            hook: 'Where does the sand go after a big swell, and how does it come back?' },
  { slug: 'tides',          theme: 'tides', min: 5, href: 'tides.html',
    title: 'Tides',                          hook: 'Why are there two high tides a day, and why do they come about 50 minutes later each day?' },
  { slug: 'sea-level',      theme: 'tides', min: 5, href: 'ssh.html',
    title: 'The ocean isn’t flat',           hook: 'Why does the sea stand higher in some places than others, and how can anyone measure it from space?' },
  { slug: 'cold-water',     theme: 'temp',  min: 4, href: 'upwelling.html',
    title: 'Cold water, green water',        hook: 'Why can the water turn cold in the middle of summer, and why does it sometimes go green?' },
  { slug: 'simulator',      theme: 'tools', min: 5, href: LIVE + '#data',
    title: 'From the forecast to the corner', hook: 'Why doesn’t the app just use the forecast’s swell height? See it in the simulator.' }
];
function chapterIndex(slug) { for (var i = 0; i < CHAPTERS.length; i++) if (CHAPTERS[i].slug === slug) return i; return -1; }

// ── Read ticks: ONE record, the reading-progress kit's (ocean-progress.js: a tick per
// section, under swim_ocean_read_v1, localStorage mirrored to a 400-day cookie; per
// device, no account, nothing sent). This file keeps no record of its own: the hub's
// ticks, "Carry on" and progress line come from the kit's summary and repaint on its
// 'oceanprogress' event. A page without the kit shows no ticks, never a second
// opinion. (The ocean_read_v1 key this replaces never shipped.)
function progress() {
  try { return window.OceanProgress ? window.OceanProgress.summary() : null; } catch (e) { return null; }
}
function onProgress(fn) {
  document.addEventListener('oceanprogress', function (e) { fn((e && e.detail) || progress()); });
  var s = progress(); if (s) fn(s);
  // the kit may load after this file: look again once everything has loaded
  window.addEventListener('load', function () { var t = progress(); if (t) fn(t); });
}

// ── Page beacon: waves.html's contract, unchanged (device id, session, referrer,
// utm, ?src= / ?from=; no PII; never throws). One event name per kind, with the
// chapter in props: ocean_open {ch}. Reads are the kit's call (its LOG_READS, off),
// so there is no automatic ocean_read. The simulator keeps
// waves_sim / waves_share so its history carries on. Only the real site logs:
// local copies, previews and headless renders send nothing.
var LOG_URL = 'https://bold-rain-6ded.sticasale.workers.dev/log';
var SESSION = null, BASE = null;
function uuid() {
  try { if (window.crypto && crypto.randomUUID) return crypto.randomUUID(); } catch (e) {}
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
    var r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}
function deviceId() {
  try {
    var id = localStorage.getItem('swim_device_id');
    if (!id) { id = uuid(); localStorage.setItem('swim_device_id', id); }
    return id;
  } catch (e) { return uuid(); }
}
function log(event, props) {
  try {
    if (location.hostname !== 'app.viz.net.au') return;
    if (!BASE) {
      SESSION = uuid(); BASE = { pwa: false, ref: '', utm: {}, src: '', from: '' };
      try { BASE.pwa = (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) || navigator.standalone === true; } catch (e) {}
      try { BASE.ref = document.referrer || ''; } catch (e) {}
      try {
        var sp = new URLSearchParams(location.search);
        ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'].forEach(function (k) {
          var v = sp.get(k); if (v) BASE.utm[k.replace('utm_', '')] = String(v).slice(0, 120);
        });
        BASE.src = String(sp.get('src') || '').slice(0, 40);
        BASE.from = String(sp.get('from') || '').slice(0, 40);
      } catch (e) {}
    }
    var pr = {}, k;
    if (BASE.src) pr.src = BASE.src;
    if (BASE.from) pr.from = BASE.from;
    if (props) for (k in props) if (Object.prototype.hasOwnProperty.call(props, k)) pr[k] = String(props[k]).slice(0, 40);
    var payload = JSON.stringify({ events: [{ device_id: deviceId(), session_id: SESSION, event: event, props: pr,
      referrer: BASE.ref, utm: BASE.utm, app_build: null, pwa: BASE.pwa }] });
    if (navigator.sendBeacon) navigator.sendBeacon(LOG_URL, new Blob([payload], { type: 'text/plain' }));
    else fetch(LOG_URL, { method: 'POST', body: payload, headers: { 'Content-Type': 'text/plain' }, keepalive: true }).catch(function () {});
  } catch (e) {}
}

// ── Chapter page frame ────────────────────────────────────────────────────────
// Fills [data-o="..."] slots, previous / next, the sticky bar's number; logs the open.
// What has been read is the kit's job: its chapter view goes at the end of the page,
// in place of the old #chRead line.
var TICK = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3.2 3L13 4.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
function fill(sel, txt) { [].forEach.call(document.querySelectorAll(sel), function (el) { el.textContent = txt; }); }
function setLink(a, c, dir, n) {
  if (!a) return;
  var k = a.querySelector('.pn-k'), t = a.querySelector('.pn-t'), h = a.querySelector('.pn-h');
  if (!c) {
    a.href = HUB;
    if (k) k.textContent = dir === 'prev' ? 'Contents' : 'That was the last chapter';
    if (t) t.textContent = 'All chapters';
    if (h) h.textContent = 'The 60-second version, and the whole list.';
    return;
  }
  a.href = c.href;
  if (k) k.textContent = dir === 'prev' ? '← Chapter ' + n : 'Next · chapter ' + n + ' →';
  if (t) t.textContent = c.title;
  if (h) h.textContent = c.hook;
}
function chapter(slug) {
  var i = chapterIndex(slug); if (i < 0) return;
  var c = CHAPTERS[i], N = CHAPTERS.length;
  fill('[data-o="n"]', String(i + 1)); fill('[data-o="of"]', String(N)); fill('[data-o="theme"]', THEMES[c.theme]);
  fill('[data-o="min"]', c.min + ' min read');
  setLink(document.getElementById('pnPrev'), CHAPTERS[i - 1], 'prev', i);
  setLink(document.getElementById('pnNext'), CHAPTERS[i + 1], 'next', i + 2);
  [].forEach.call(document.querySelectorAll('[data-o="hub"]'), function (a) { a.href = HUB; });
  [].forEach.call(document.querySelectorAll('[data-o="app"]'), function (a) { a.href = APP; });
  stickyToc();
  log('ocean_open', { ch: slug });
}
// the sticky "in this chapter" bar: highlights the part you're in, keeps that chip
// in view in its own scroller, and shows how far through the chapter you are. Parts
// hidden by the kit's "Hide what I've read" are skipped (a display:none section has no
// boxes, and its rect top of 0 would read as "scrolled past"); the kit's 'oceanprogress'
// event, sent after every hide and show, re-checks. The bar sticks at its CSS top,
// which is the status bar's height inside the installed iPhone app and 0 elsewhere.
function stickyToc() {
  var nav = document.querySelector('.ch-toc'); if (!nav) return;
  document.body.classList.add('has-toc');
  var list = nav.querySelector('ol'), bar = nav.querySelector('.ch-toc-bar i'), main = document.querySelector('main');
  var links = [].slice.call(nav.querySelectorAll('a.chip-link'));
  var secs = links.map(function (a) { return document.getElementById(a.getAttribute('href').slice(1)); });
  var cur = -1, queued = false;
  function stickTop() { return parseFloat(window.getComputedStyle(nav).top) || 0; }
  function update() {
    queued = false;
    var off = stickTop() + nav.offsetHeight + 40, k = -1;
    for (var j = 0; j < secs.length; j++) {
      var s = secs[j];
      if (!s || !s.getClientRects().length) continue;    // hidden: not a place you can be
      if (k < 0 || s.getBoundingClientRect().top - off <= 0) k = j;
    }
    if (k !== cur) {
      cur = k;
      links.forEach(function (a, j) { if (j === k) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current'); });
      var a = links[k];
      if (a && list && list.scrollWidth > list.clientWidth) {
        var l = a.offsetLeft - list.offsetLeft, r = l + a.offsetWidth;
        if (l < list.scrollLeft || r > list.scrollLeft + list.clientWidth) list.scrollLeft = Math.max(0, l - 24);
      }
    }
    if (bar && main) {
      var rc = main.getBoundingClientRect(), span = rc.height - window.innerHeight;
      var p = span > 0 ? Math.min(1, Math.max(0, -rc.top / span)) : 1;
      bar.style.transform = 'scaleX(' + p.toFixed(3) + ')';
    }
    var st = nav.getBoundingClientRect().top <= stickTop() + 0.5;
    if (st !== nav.classList.contains('stuck')) nav.classList.toggle('stuck', st);
  }
  function queue() { if (!queued) { queued = true; requestAnimationFrame(update); } }
  window.addEventListener('scroll', queue, { passive: true });
  window.addEventListener('resize', queue);
  document.addEventListener('oceanprogress', queue);
  update();
}

// ── Hub ───────────────────────────────────────────────────────────────────────
// A chapter counts as read when the kit has every one of its sections ticked. Chapters
// still being written have no sections in the kit's summary, so they are never counted
// and "Carry on" never points at one.
function hub() {
  var cardEls = [].slice.call(document.querySelectorAll('[data-slug]:not([data-ref])'));
  var go = document.getElementById('hubGo'), prog = document.getElementById('hubProgress');
  var goHref = go ? go.getAttribute('href') : '', goHtml = go ? go.innerHTML : '';
  onProgress(function (sum) {
    var chs = (sum && sum.chapters) || {}, done = 0, total = 0, firstUnread = -1;
    CHAPTERS.forEach(function (c, i) {
      var p = chs[c.slug];
      if (!p || !p.total) return;
      total++;
      if (p.done === p.total) done++; else if (firstUnread < 0) firstUnread = i;
    });
    cardEls.forEach(function (card) {
      var p = chs[card.getAttribute('data-slug')], on = !!p && p.total > 0 && p.done === p.total;
      card.classList.toggle('is-read', on);
      var t = card.querySelector('.tick');
      if (t) { if (on) t.innerHTML = TICK + ' Read'; t.hidden = !on; }
    });
    if (go) {
      if (!sum || !sum.done) { go.setAttribute('href', goHref); go.innerHTML = goHtml; }
      else {
        var nx = firstUnread < 0 ? 0 : firstUnread;
        go.href = CHAPTERS[nx].href;
        go.textContent = firstUnread < 0 ? 'Read again from chapter 1' : 'Carry on: chapter ' + (nx + 1) + ' →';
      }
    }
    if (prog) {
      if (sum && sum.done && total) {
        prog.innerHTML = 'You’ve read ' + done + ' of ' + total + ' chapters on this device.' +
          '<span class="pbar" aria-hidden="true"><i style="width:' + Math.round(done / total * 100) + '%"></i></span>';
        prog.hidden = false;
      } else prog.hidden = true;
    }
  });
  var cards = [].map.call(document.querySelectorAll('[data-slug]:not([data-ref])'), function (c) { return c.getAttribute('data-slug'); });
  if (cards.join() !== CHAPTERS.map(function (c) { return c.slug; }).join()) {
    try { console.warn('[ocean] the hub cards and CHAPTERS are out of step', cards); } catch (e) {}
  }
  heroWave(document.getElementById('heroCv'));
  log('ocean_open', { ch: 'hub' });
}

// ── Exports ──────────────────────────────────────────────────────────────────
var O = window.OCEAN = {
  G: G, REDUCED: REDUCED,
  ang: ang, interp: interp, compass: compass, waveK: waveK, groupSpeed: groupSpeed, spmGrowth: spmGrowth,
  runCanvas: runCanvas, heroWave: heroWave,
  MODEL: MODEL, dirShare: dirShare, mouthH: mouthH, ssTransmit: ssTransmit, leeFactor: leeFactor, fetchFor: fetchFor,
  live: live, cfg: {},
  CHAPTERS: CHAPTERS, THEMES: THEMES, chapterIndex: chapterIndex,
  progress: progress, log: log, chapter: chapter, hub: hub
};
// The live-overridable settings, read at the moment of use: liveSettings() swaps new
// values into the variables above and then fires 'wavesconfig'. A chapter that copies
// one into a local variable re-reads it in its own 'wavesconfig' listener.
Object.defineProperties(O.cfg, {
  SS:        { enumerable: true, get: function () { return SS; } },
  FETCH:     { enumerable: true, get: function () { return FETCH; } },
  CHOP_GAIN: { enumerable: true, get: function () { return CHOP_GAIN; } },
  LEE:       { enumerable: true, get: function () { return LEE; } },
  LEE_FROM:  { enumerable: true, get: function () { return LEE_FROM; } },
  LEE_TO:    { enumerable: true, get: function () { return LEE_TO; } },
  LEE_TAPER: { enumerable: true, get: function () { return LEE_TAPER; } },
  WALL_LO:   { enumerable: true, get: function () { return WALL_LO; } },
  WALL_HI:   { enumerable: true, get: function () { return WALL_HI; } },
  WALL_R:    { enumerable: true, get: function () { return WALL_R; } },
  SAVED_ON:  { enumerable: true, get: function () { return SAVED_ON; } }
});
})();
