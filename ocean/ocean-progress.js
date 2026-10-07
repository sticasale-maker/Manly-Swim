/* ocean-progress.js: reading progress for the ocean explainer. PROTOTYPE 7 Oct 2026.
   An "I've read this" button at the end of each section, a contents list with ticks
   and a progress bar, and one sentence beside it that asks the question the next
   unread section answers. Works on one long page (waves.html today) and across the
   hub + chapter pages (one shared store). ES5, no libraries, no build step.
   Pair with ocean-progress.css.

   HOW A PAGE USES IT
     <link rel="stylesheet" href="ocean-progress.css">
     <div data-op-contents="page"> ...no-script fallback, e.g. the old nav.toc... </div>
     <script src="ocean-progress.js" defer></script>
   data-op-contents names the view, and a page may hold more than one:
     "page"     the registered sections found on this page, numbered 01, 02 ... (waves.html)
     "hub"      every section of every chapter, grouped, each chapter with its own x/y
     "chapter"  with data-op-chapter="<slug>": that chapter's sections, overall progress
   In "page" and "chapter" views every registered <section id> on the page gets a read
   button at its end (or in its [data-op-read] slot, if it has one). A "page" view also
   gets a slim contents bar that slides down once its panel scrolls away
   (data-op-mini="off" to drop it; "on" to add it to a chapter view).
   Links to other pages come from window.OCEAN_PROGRESS, set before this file loads:
     { base: '', hub: './', hrefs: { <chapter slug>: '<url>' }, log: false, paramsHash: 'sim' }
   (see hrefFor). Pages can listen for the 'oceanprogress' event on document, or call
   window.OceanProgress (see the end).

   HIDE WHAT I'VE READ (owner's request, 7 Oct 2026)
     Above each contents list sits a two-button switch, [Show everything] [Hide what I've
     read]: real buttons with aria-pressed, the same pattern as waves.html's figure
     switches (.seg). It is one setting for the device, kept in the same store as the ticks
     under its own key (HKEY), and it starts on "Show everything". While hiding:
     - a read <section> gets .op-hid (display:none), so the page shows only what is left;
     - the contents still list every section, ticked. Tapping one (or any link or hash that
       points into a hidden section, a shared simulator link included) shows just that
       section for this visit, then the browser scrolls to it. "For this visit" is this
       tab: the list is kept in sessionStorage, so a reload or a tab the phone put to sleep
       comes back with the same sections showing (and so in the same place);
     - a section ticked while hiding is not whisked away under the thumb: "Hiding it in a
       moment" and an Undo button sit by its button, and after PEND_MS it goes. If you are
       still at its button, it fades and the next thing slides up into its place (no
       slide under reduced motion). If you have scrolled on, whatever you are reading
       stays exactly where it is; if you are back inside it, it waits until you leave;
     - with nothing left, one calm line and a Show everything button, plus, on a chapter
       page, the next chapter that has something unread;
     - a chapter page says, above its first part still showing, which parts are hidden
       ("Part 1, which you've read, is hidden. [Show it]");
     - hub cards opt in with data-op-card="<slug>" and hide once that chapter is all read;
       a [data-op-cardgroup] wrapper (a theme) hides when every card in it has, and a line
       is left where its cards were ("Waves: 4 chapters you've read are hidden. [Show them]").
     Changes are announced politely ("Hidden: Sets." / "Shown: Sets."), and a shown section
     gets a window 'resize' so its canvases re-measure, as the app's bottom tabs do.
     A reload or Back never jumps to the section named in the address: only a fresh visit
     to a link does. The browser puts you back where you were.

   RULES IT KEEPS (CLAUDE.md)
   §4  No accounts. Ticks and the hide switch belong to this device: localStorage,
       mirrored to a 400-day cookie by the app's own persistGet/persistSet (Safari's ITP
       empties localStorage after 7 idle days). What is "shown for now" belongs to the
       tab: sessionStorage, gone when the tab closes. Versioned keys. Every access is
       wrapped: with storage blocked all three still work for the visit, and the page
       says ticks won't be kept. Nothing is sent anywhere: the analytics hook (logRead)
       is OFF.
   §1  Physics only. The teasers are curiosity about the physics: never about whether to
       swim, never reassurance. Progress stays calm: ticks and a count. No streaks, no
       badges, no "don't lose your progress", nothing timed.
   §2  No teaser quotes a modelled height inside the bay, or says the point "breaks".
*/
(function () {
'use strict';

// ── REGISTRY: the one list. Reading order is this order. ─────────────────────
// Chapters follow the hub prototype (proto/hub.html and ocean.js CHAPTERS: same slugs,
// same order). soon: true = not published yet. The hub lists it as being written, but
// no count includes it and no teaser offers it. Flip it off when its page ships.
// Sections: id is the <section id> on whichever page holds it (waves.html today, a
// chapter page later), so a tick carries over when a section moves page. The runway
// rewrite of section 10 (proto/fetch.html) keeps id "wind": the same section, one tick.
//   q  the teaser: a question the section's OWN text answers, set at South Steyne, the
//      ramp, the sea wall, Shelly or the bay wherever the physics there allows it.
//   a  a short phrase copied word for word from the sentence that answers q. check_teasers.py
//      fails if the phrase goes missing from that <section>, so a rewrite of the prose
//      cannot leave its teaser unanswered without someone noticing.
//   provisional: q was written before its section existed. It is never shown (the
//      teaser falls back to the title) until someone reads the real prose, checks q,
//      adds its a, and deletes the flag.
var REGISTRY = {
  chapters: [
    { ch: 'waves',          title: 'What a wave is' },
    { ch: 'swell-and-sets', title: 'Swell, sea and sets' },
    { ch: 'into-the-bay',   title: 'How swell gets into the bay' },
    { ch: 'point-and-wall', title: 'The point and the sea wall' },
    { ch: 'wind',           title: 'Wind and chop' },
    { ch: 'currents',       title: 'Carried along' },
    { ch: 'sand',           title: 'Where the sand goes' },
    { ch: 'tides',          title: 'Tides' },
    { ch: 'sea-level',      title: 'The ocean isn’t flat' },
    { ch: 'cold-water',     title: 'Cold water, green water' },
    { ch: 'simulator',      title: 'From the forecast to the corner' }
  ],
  sections: [
    { id: 'energy',     ch: 'waves',          title: 'A wave is energy',
      q: 'When a swell rolls past the ramp, where does the water actually go?',
      a: 'comes back to nearly where it started' },
    { id: 'size',       ch: 'waves',          title: 'Height & period',
      q: 'How far down can a swell feel the seabed off Manly?',
      a: 'scraping the bottom across the whole shelf off Manly' },
    { id: 'swell-sea',  ch: 'swell-and-sets', title: 'Swell vs sea',
      q: 'Where was the swell rolling into South Steyne made, and when?',
      a: 'often days ago and hundreds of kilometres away' },
    { id: 'sets',       ch: 'swell-and-sets', title: 'Sets',
      q: 'Why do waves reach South Steyne in sets, with lulls in between?',
      a: 'Where their crests line up they add' },
    { id: 'shoaling',   ch: 'into-the-bay',   title: 'Feeling the bottom',
      q: 'Why can a swell you barely notice from the promenade rear up at the sand?',
      a: 'barely noticed from the promenade can rear up' },
    { id: 'bending',    ch: 'into-the-bay',   title: 'Bending round',
      q: 'How can a south-east swell turn the corner round Shelly headland?',
      a: 'has to swing round that headland to get in' },
    { id: 'point',      ch: 'point-and-wall', title: 'The point',
      q: 'Why does the point along from South Steyne gather more swell than the bay beside it?',
      a: 'The point gets more swell' },
    { id: 'bounce',     ch: 'point-and-wall', title: 'Bouncing back',
      q: 'Why can the slosh off the sea wall nearly double at high tide?',
      a: 'the slosh can be close to double' },
    { id: 'wind',       ch: 'wind',           title: 'Wind',
      q: 'Why does a north-easter make more chop in the bay than a southerly just as strong?',
      a: '50 to 120 m' },
    { id: 'drift',      ch: 'currents',       title: 'Carried along',
      q: 'Out at sea, what moves the surface faster: a one-metre swell or a 20 km/h breeze?',
      a: 'more than 20 times the creep' },
    { id: 'sand',        ch: 'sand',           title: 'Where the sand goes',
      q: 'After a big storm strips South Steyne down to the rocks, where did the sand actually go, and how does it get back?',
      a: 'storm waves move it a short way out to sea, and calmer swell can bring it back' },
    { id: 'sand-swings', ch: 'sand',           title: 'The beach swings',
      q: 'Why does the north end of Manly Beach move so much more than the South Steyne end?',
      a: 'swings like a gate on a hinge near South Steyne' },
    { id: 'tides',       ch: 'tides',          title: 'Two high tides a day',
      q: 'If the Moon doesn’t lift the sea, why does the water at the South Steyne ramp rise and fall twice a day, about 50 minutes later each day?',
      a: 'so Manly passes through both' },
    { id: 'manly-tide',  ch: 'tides',          title: 'The tide at Manly',
      q: 'There’s no tide gauge in Cabbage Tree Bay, so where does Manly’s tide come from?',
      a: 'So Manly’s tides come at about Fort Denison’s times' },
    { id: 'hills',       ch: 'sea-level',      title: 'Hills and dips',
      q: 'From the Marine Parade wall on a still morning the sea looks level. Is it?',
      a: 'rises into gentle hills and sinks into gentle dips' },
    { id: 'manly',       ch: 'sea-level',      title: 'Why the hills matter',
      q: 'How can a storm lift the whole sea at Manly, not just the waves on top?',
      a: 'Low pressure and onshore wind together lift the whole sea' },
    { id: 'measure',     ch: 'sea-level',      title: 'Measuring from space',
      q: 'Can a satellite in space see a 25 cm hill on the sea, and why can’t it see Cabbage Tree Bay?',
      a: 'so they never see Cabbage Tree Bay' },
    { id: 'upwelling',   ch: 'cold-water',     title: 'Cold water from below',
      q: 'Why can a warm north-easter leave South Steyne colder, and then greener, a few days later?',
      a: 'cold water from below comes up to take its place' },
    { id: 'green-water', ch: 'cold-water',     title: 'Green water',
      q: 'Why does cold water from deep down turn the bay green, and why does it take days?',
      a: 'it is also rich in nutrients' },
    { id: 'data',       ch: 'simulator',      title: 'What the data says',
      q: 'Why doesn’t the app just use the forecast’s swell height for the bay?',
      a: 'the app doesn’t use the forecast height directly' },
    { id: 'sim',        ch: 'simulator',      title: 'Simulator',
      q: 'Which swell direction gets the most energy round to the South Steyne corner?',
      a: 'round the corner to the South Steyne entry' }
  ]
};
// NB on waves.html the order is the page's own (01-12): drift sits before wind there.
// The registry follows the hub's chapter order, and a "page" view lists what is on the
// page in DOCUMENT order, so both read naturally.

var CFG = window.OCEAN_PROGRESS || {};
var REG = CFG.registry || REGISTRY;
var KEY = 'swim_ocean_read_v1';                       // the app's key style: swim_<what>_v<n>
var HKEY = 'swim_ocean_hide_v1';                      // "on" | "off" (never ''), default off
var SKEY = 'swim_ocean_shown_v1';                     // sessionStorage: shown again in this tab
var LOG_READS = false;                                // see logRead: OFF until the owner says
var PEND_MS = 5000;                                   // a section ticked while hiding stays this long
var ID_OK = /^[a-z0-9-]{1,40}$/;                      // what the store can hold (see parse)
function warn(t) { try { if (window.console && console.warn) console.warn('[ocean-progress] ' + t); } catch (e) {} }
// The registry is checked once, here. A section the store could not hold (an id with a
// capital, an underscore or a dot, or over 40 characters), one whose chapter is not in
// chapters, or a second entry with the same id is skipped with a console warning: its
// button would otherwise tick and never stay ticked, or be counted on one page and not
// another. check_teasers.py runs the same checks, so a rename fails before it ships.
var CH = {}, SEC = {}, SECTIONS = [];
REG.chapters.forEach(function (c, i) { CH[c.ch] = { c: c, n: i + 1 }; });
REG.sections.forEach(function (s) {
  var id = s && s.id;
  if (typeof id !== 'string' || !ID_OK.test(id)) { warn('skipped section "' + id + '": an id must be 1 to 40 lowercase letters, digits or hyphens'); return; }
  if (!CH[s.ch]) { warn('skipped section "' + id + '": its chapter "' + s.ch + '" is not in chapters'); return; }
  if (SEC[id]) { warn('skipped a second section "' + id + '": ids must be unique'); return; }
  SEC[id] = s;
  SECTIONS.push(s);
});

// ── DURABLE STORE: the app's persistGet / persistSet (index.html, "DURABLE PREFERENCE
// STORE"), same names, same cookie (path=/, 400 days, SameSite=Lax). localStorage is
// primary; a read that misses it falls back to the cookie and rehydrates. A host page
// that already defines window.persistGet/persistSet (index.html) is used as is. ──────
function _ckGet(name) {
  try {
    var m = document.cookie.match('(?:^|;\\s*)' + name + '=([^;]*)');
    return m ? decodeURIComponent(m[1]) : null;
  } catch (e) { return null; }
}
function _ckSet(name, val) {
  try { document.cookie = name + '=' + encodeURIComponent(val) + ';path=/;max-age=34560000;SameSite=Lax'; } catch (e) {}
}
function persistGet(key) {
  var v = null;
  try { v = localStorage.getItem(key); } catch (e) {}
  if (v == null || v === '') {
    var c = _ckGet(key);
    if (c != null && c !== '') { v = c; try { localStorage.setItem(key, c); } catch (e) {} }
  }
  return v;
}
function persistSet(key, val) {
  try { localStorage.setItem(key, val); } catch (e) {}
  _ckSet(key, val);
}
var pGet = typeof window.persistGet === 'function' ? window.persistGet : persistGet;
var pSet = typeof window.persistSet === 'function' ? window.persistSet : persistSet;

// Can this browser keep anything at all? (private modes, blocked site data). If not,
// ticks and the switch live in memory for the visit and the contents footer says so.
var KEEPS = (function probe() {
  var t = 'swim_op_probe', ok = false;
  try { localStorage.setItem(t, '1'); ok = localStorage.getItem(t) === '1'; localStorage.removeItem(t); } catch (e) {}
  if (ok) return true;
  try {
    document.cookie = t + '=1;path=/;max-age=60;SameSite=Lax';
    ok = document.cookie.indexOf(t + '=1') > -1;
    document.cookie = t + '=;path=/;max-age=0;SameSite=Lax';
  } catch (e) {}
  return ok;
})();
var mem = {}, memHide = false;

// Stored value: "v1.energy.size.bending" (ids joined by dots: none of them is escaped
// by encodeURIComponent, so the cookie stays short). "v1" alone = nothing read; it is
// never empty, because persistGet reads '' as "missing" and would fall back to an older
// cookie. Unknown ids are kept (a section may come back), just never counted.
function parse(v) {
  var m = {}, p = String(v || '').split('.');
  if (p[0] !== 'v1') return m;
  for (var i = 1; i < p.length; i++) if (/^[a-z0-9-]{1,40}$/.test(p[i])) m[p[i]] = 1;
  return m;
}
function serial(m) {
  var out = ['v1'];
  for (var k in m) if (Object.prototype.hasOwnProperty.call(m, k) && m[k]) out.push(k);
  return out.join('.');
}
function copy(m) { var o = {}; for (var k in m) if (Object.prototype.hasOwnProperty.call(m, k)) o[k] = m[k]; return o; }
function load() {                                      // read fresh every time: other tabs write too
  if (KEEPS) { try { var v = pGet(KEY); if (v != null) mem = parse(v); } catch (e) {} }
  return copy(mem);
}
function save(m) {
  mem = copy(m);
  if (KEEPS) { try { pSet(KEY, serial(m)); } catch (e) {} }
}
// The switch: "on" = hide what I've read. Missing = off, the default.
function hideOn() {
  if (KEEPS) { try { var v = pGet(HKEY); if (v === 'on' || v === 'off') memHide = v === 'on'; } catch (e) {} }
  return memHide;
}
function saveHide(on) {
  memHide = !!on;
  if (KEEPS) { try { pSet(HKEY, on ? 'on' : 'off'); } catch (e) {} }
}

// ── ANALYTICS HOOK: OFF. The page already sends waves_open (and the simulator
// waves_sim / waves_share) through window.wavesLog; the chapter pages send ocean_open /
// ocean_read through OCEAN.log. Whether a tick is also counted as 'section_read' is the
// owner's call: set LOG_READS above (or window.OCEAN_PROGRESS.log = true). It would send
// the first tick of each section per visit, with the section id and nothing else, and
// never an untick. waves.html's wavesLog(event) takes no props, so it would need a
// props argument (OCEAN.log already has one) before the id could go with it. The hide
// switch is never logged. ─────────────────────────────────────────────────────────
var logged = {};
function logRead(id) {
  if (!(LOG_READS || CFG.log === true) || logged[id]) return;
  logged[id] = 1;
  try {
    if (window.OCEAN && typeof window.OCEAN.log === 'function') window.OCEAN.log('section_read', { s: id });
    else if (typeof window.wavesLog === 'function') window.wavesLog('section_read');
  } catch (e) {}
}

// ── Small helpers ────────────────────────────────────────────────────────────
function esc(s) {
  return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; });
}
function pad(n) { return n < 10 ? '0' + n : String(n); }
function closest(el, sel) {
  while (el && el.nodeType === 1) {
    var f = el.matches || el.msMatchesSelector || el.webkitMatchesSelector;
    if (f && f.call(el, sel)) return el;
    el = el.parentNode;
  }
  return null;
}
function $all(sel, root) { return [].slice.call((root || document).querySelectorAll(sel)); }
function rendered(el) {                                 // in the page and taking up room
  return !!el && el.nodeType === 1 && document.documentElement.contains(el) && el.getClientRects().length > 0;
}
function reduced() {
  try { return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches); } catch (e) { return false; }
}
var raf = window.requestAnimationFrame ? function (f) { window.requestAnimationFrame(f); } : function (f) { setTimeout(f, 16); };
function reflow(el) { return el.offsetHeight; }
function softFocus(el) {                                // focus without scrolling the page
  if (!el) return;
  if (!el.hasAttribute('tabindex') && !/^(A|BUTTON|INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) el.setAttribute('tabindex', '-1');
  try { el.focus({ preventScroll: true }); } catch (e) { try { el.focus(); } catch (e2) {} }
}
// The app's bottom tabs re-fit their canvases on 'resize'; waves.html's runCanvas
// re-measures on it too, so a section that was display:none gets its real width back.
function fireResize() {
  var ev;
  try { ev = new Event('resize'); }
  catch (e) { try { ev = document.createEvent('UIEvents'); ev.initUIEvent('resize', true, false, window, 0); } catch (e2) { return; } }
  try { window.dispatchEvent(ev); } catch (e) {}
}
var ICON = '<svg class="op-ico" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' +
  '<circle cx="12" cy="12" r="9.2"/><path d="M7.6 12.5l3 2.9 5.9-6.2"/></svg>';
var CHEV = '<svg class="op-chev" viewBox="0 0 16 16" aria-hidden="true" focusable="false">' +
  '<path d="M3.5 6l4.5 4.5L12.5 6"/></svg>';

// Where a section lives. On this page: its own anchor. Elsewhere: its chapter's page
// (hrefs[slug] if given, else base + slug + '.html', the production layout
// /Manly-Swim/ocean/<slug>.html), plus #id. hrefs may carry a hash for the chapter
// link itself (e.g. 'waves.html#energy' while a chapter still lives inside waves.html).
var LOCAL = {};                                         // id -> its <section> on this page
function chapterHref(slug) {
  var h = CFG.hrefs && CFG.hrefs[slug];
  return h || (CFG.base || '') + slug + '.html';
}
function hrefFor(s, kind) {
  if (kind !== 'hub' && LOCAL[s.id]) return '#' + s.id;
  return chapterHref(s.ch).split('#')[0] + '#' + s.id;
}
function hubHref() { return CFG.hub || (CFG.base || '') + './'; }

// Published sections in reading order. A soon chapter's section still counts on a page
// that actually holds it (its own prototype page, say).
function avail(s) { return !!CH[s.ch] && (!CH[s.ch].c.soon || !!LOCAL[s.id]); }
function qFor(s) { return s.provisional ? '' : s.q; }  // an unchecked question is never shown

// ── VIEWS ────────────────────────────────────────────────────────────────────
var views = [], mini = null, liveTaken = false;

function scopeFor(kind, slug) {
  var all = SECTIONS.filter(avail), list, start = 0;
  if (kind === 'page') {
    // what this page holds, in the page's own order
    list = SECTIONS.filter(function (s) { return !!LOCAL[s.id]; });
    list.sort(function (a, b) {
      var p = LOCAL[a.id].compareDocumentPosition(LOCAL[b.id]);
      return (p & 4) ? -1 : (p & 2) ? 1 : 0;
    });
    all = list;
  } else if (kind === 'chapter') {
    list = all.filter(function (s) { return s.ch === slug; });
    for (var i = 0; i < all.length; i++) if (all[i].ch === slug) { start = i; break; }
  } else {
    list = all;
  }
  return { list: list, all: all, start: start };
}

function rowHtml(s, num, kind) {
  return '<li class="op-row" data-op-row="' + esc(s.id) + '"><a href="' + esc(hrefFor(s, kind)) + '">' +
    (num ? '<span class="op-n">' + num + '</span>' : '') +
    '<span class="op-t">' + esc(s.title) + '</span>' +
    '<span class="op-tag" aria-hidden="true" hidden>Next</span>' +
    '<span class="op-st">' + ICON + '<span class="op-vh"></span></span></a></li>';
}
// The switch: two real buttons, one pressed, like waves.html's .seg figure switches.
function segHtml(what) {
  return '<div class="op-hide" hidden><div class="op-seg" role="group" aria-label="' + esc(what) + '">' +
    '<button type="button" class="op-seg-b" data-op-hide="0" aria-pressed="true">Show everything</button>' +
    '<button type="button" class="op-seg-b" data-op-hide="1" aria-pressed="false">Hide what I’ve read</button>' +
    '</div><p class="op-hide-st" hidden></p></div>';
}
function takeSeg(v, root) {
  v.hideBox = root.querySelector('.op-hide');
  if (!v.hideBox) return;
  v.segBtns = $all('.op-seg-b', v.hideBox);
  v.hideSt = v.hideBox.querySelector('.op-hide-st');
}
function hasLocal(list) { for (var i = 0; i < list.length; i++) if (LOCAL[list[i].id]) return true; return false; }

function buildView(el, kind, slug) {
  var sc = scopeFor(kind, slug);
  if (!sc.list.length && kind !== 'hub') return null;
  var v = { el: el, kind: kind, ch: slug, list: sc.list, all: sc.all, start: sc.start,
            asking: false, resetShown: null, nextKey: null };
  // No live region on the count: [data-op-say] is the page's one voice, and a tap says the
  // new count itself (tapSay). Background refreshes (another tab, coming back) stay silent.
  var live = '';
  var seg = kind === 'hub' ? (document.querySelector('[data-op-card]') ? segHtml('Chapters you’ve read') : '')
    : hasLocal(sc.list) ? segHtml('Sections you’ve read') : '';
  var h = '';
  if (kind === 'chapter') {
    var cc = CH[slug];
    h += '<nav class="op-panel op-chapter" aria-label="Reading progress, this chapter">' +
      '<div class="op-top"><p class="op-h">In this chapter</p><p class="op-count"' + live + '></p></div>' + seg +
      '<ol class="op-list">' + sc.list.map(function (s) { return rowHtml(s, '', kind); }).join('') + '</ol>' +
      '<div class="op-all"><div class="op-meter" role="progressbar" aria-label="Sections read, all chapters" aria-valuemin="0"><i></i></div>' +
      '<p class="op-all-line"><span class="op-all-count"></span> <a class="op-all-link" href="' + esc(hubHref()) + '">All chapters <span aria-hidden="true">&rarr;</span></a></p></div>' +
      '<div class="op-next-slot"></div>' +
      '<p class="op-note"></p></nav>';
    v.label = cc ? cc.c.title : '';
  } else {
    h += '<nav class="op-panel op-' + kind + '" aria-label="Contents">' +
      '<div class="op-top"><p class="op-h">Contents</p><p class="op-count"' + live + '></p></div>' +
      '<div class="op-meter" role="progressbar" aria-label="Sections read" aria-valuemin="0"><i></i></div>' +
      '<div class="op-next-slot"></div>' + seg;
    if (kind === 'hub') {
      h += '<ol class="op-chs">' + REG.chapters.map(function (c, i) {
        var secs = sc.list.filter(function (s) { return s.ch === c.ch; });
        if (c.soon && !secs.length) {
          return '<li class="op-ch is-soon"><div class="op-ch-h"><span class="op-ch-n">' + (i + 1) + '</span>' +
            '<span class="op-ch-t">' + esc(c.title) + '</span><span class="op-soon">Being written</span></div></li>';
        }
        return '<li class="op-ch" data-op-chrow="' + esc(c.ch) + '"><div class="op-ch-h"><span class="op-ch-n">' + (i + 1) + '</span>' +
          '<a class="op-ch-t" href="' + esc(chapterHref(c.ch)) + '">' + esc(c.title) + '</a>' +
          '<span class="op-ch-c"></span><span class="op-st">' + ICON + '</span></div>' +
          '<ol class="op-list">' + secs.map(function (s) { return rowHtml(s, '', kind); }).join('') + '</ol></li>';
      }).join('') + '</ol>';
    } else {
      h += '<ol class="op-list">' + sc.list.map(function (s, i) { return rowHtml(s, pad(i + 1), kind); }).join('') + '</ol>';
    }
    h += '<div class="op-foot"><p class="op-note"></p></div></nav>';
  }
  el.innerHTML = h;
  el.classList.add('op-on');
  v.root = el.firstChild;
  v.count = el.querySelector('.op-count');
  v.meter = el.querySelector('.op-meter');
  v.allCount = el.querySelector('.op-all-count');
  v.nextSlot = el.querySelector('.op-next-slot');
  v.note = el.querySelector('.op-note');
  v.foot = el.querySelector('.op-foot');
  takeSeg(v, el);
  if (v.foot) {                                          // a chapter view has no reset: that is the hub's
    v.resetEl = document.createElement('div');
    v.resetEl.className = 'op-reset';
    v.resetEl.hidden = true;
    v.foot.appendChild(v.resetEl);
  }
  return v;
}

// The slim bar for long pages: where you are, how many read, the switch, the same list.
function buildMini(src) {
  var bar = document.createElement('div');
  bar.className = 'op-mini';
  bar.setAttribute('data-op-mini-bar', '');
  bar.innerHTML = '<nav class="op-mini-in" aria-label="Contents, compact">' +
    '<button type="button" class="op-mini-btn" aria-expanded="false" aria-controls="op-mini-panel">' +
    '<span class="op-mini-where">Contents</span><span class="op-mini-count"></span>' + CHEV + '</button>' +
    '<div class="op-mini-bar" aria-hidden="true"><i></i></div>' +
    '<div class="op-mini-panel" id="op-mini-panel" hidden><div class="op-mini-scroll">' +
    '<div class="op-next-slot"></div>' + (src.hideBox ? segHtml('Sections you’ve read') : '') +
    '<ol class="op-list">' + src.list.map(function (s, i) {
      return rowHtml(s, src.kind === 'page' ? pad(i + 1) : '', src.kind);
    }).join('') + '</ol></div></div></nav>';
  document.body.appendChild(bar);
  document.documentElement.classList.add('op-has-mini');
  var v = { el: bar, kind: src.kind, ch: src.ch, list: src.list, all: src.all, start: src.start,
            isMini: true, src: src, open: false, nextKey: null };
  v.btn = bar.querySelector('.op-mini-btn');
  v.where = bar.querySelector('.op-mini-where');
  v.count = bar.querySelector('.op-mini-count');
  v.line = bar.querySelector('.op-mini-bar i');
  v.panel = bar.querySelector('.op-mini-panel');
  v.nextSlot = bar.querySelector('.op-next-slot');
  takeSeg(v, bar);
  return v;
}
function setMini(v, open, focusBtn) {
  v.open = open;
  v.panel.hidden = !open;
  v.btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  v.el.classList.toggle('is-open', open);
  if (!open && focusBtn) v.btn.focus();
}

// ── PAINT ────────────────────────────────────────────────────────────────────
function countIn(list, m) { var n = 0; for (var i = 0; i < list.length; i++) if (m[list[i].id]) n++; return n; }
function nextOf(v, m) {
  var a = v.all, n = a.length;
  for (var i = 0; i < n; i++) { var s = a[(v.start + i) % n]; if (!m[s.id]) return s; }
  return null;
}
function soonCount() { var n = 0; REG.chapters.forEach(function (c) { if (c.soon) n++; }); return n; }
// page: "04 · Sets"; hub, or another chapter: "Chapter 2 · Sets"; this chapter: "Sets"
function whereFor(v, nx) {
  return v.kind === 'page' ? pad(v.list.indexOf(nx) + 1) + ' · ' + nx.title
    : (v.kind === 'hub' || nx.ch !== v.ch) && CH[nx.ch] ? 'Chapter ' + CH[nx.ch].n + ' · ' + nx.title
    : nx.title;
}
function nextLink(v, nx, label) {
  var q = qFor(nx);
  return '<a class="op-next" href="' + esc(hrefFor(nx, v.kind)) + '">' +
    '<span class="op-next-k">' + esc(label) + '</span>' +
    (q ? '<span class="op-next-q">' + esc(q) + '</span>' : '') +
    '<span class="op-next-t">' + esc(whereFor(v, nx)) + ' <span aria-hidden="true">&rarr;</span></span></a>';
}

function paintRows(root, m, nx) {
  $all('[data-op-row]', root).forEach(function (li) {
    var id = li.getAttribute('data-op-row'), on = !!m[id], isNext = !!nx && nx.id === id;
    var hid = !!LOCAL[id] && LOCAL[id].classList.contains('op-hid');
    li.classList.toggle('is-read', on);
    li.classList.toggle('is-next', isNext);
    li.classList.toggle('is-hid', hid);
    var tag = li.querySelector('.op-tag'); if (tag) tag.hidden = !isNext;
    var vh = li.querySelector('.op-st .op-vh');
    if (vh) vh.textContent = on ? (hid ? ', read, hidden' : ', read') : (isNext ? ', next' : ', not read yet');
  });
}
function paintMeter(meter, done, total) {
  if (!meter) return;
  meter.setAttribute('aria-valuemax', String(total));
  meter.setAttribute('aria-valuenow', String(done));
  meter.setAttribute('aria-valuetext', done + ' of ' + total + ' read');
  var i = meter.querySelector('i'); if (i) i.style.width = (total ? Math.round(done / total * 1000) / 10 : 0) + '%';
}
// The teaser. Rebuilt only when its target changes, so focus inside it survives a
// repaint from another tab.
function calmNext(v) {                                 // this view's calm card already asks "Next up"
  for (var i = 0; i < EMPTIES.length; i++) if (EMPTIES[i].v === v && !EMPTIES[i].el.hidden && /\|./.test(EMPTIES[i].key || '')) return true;
  return false;
}
function paintNext(v, m, done, total) {
  var nx = nextOf(v, m), calm = v.kind === 'chapter' && calmNext(v);
  var key = calm ? 'calm' : nx ? nx.id + '|' + (done ? 1 : 0) : 'done|' + total;
  if (calm) { if (key !== v.nextKey) { v.nextKey = key; v.nextSlot.innerHTML = ''; } return nx; }
  if (key === v.nextKey) return nx;
  v.nextKey = key;
  if (nx) {
    v.nextSlot.innerHTML = nextLink(v, nx, done ? 'Next up' : 'Start here');
  } else {
    var soon = v.kind !== 'page' ? soonCount() : 0;
    var line = v.kind === 'page'
      ? 'You’ve read all ' + total + ' sections.'
      : 'You’ve read every section so far' + (soon ? '.' : ', all ' + total + '.');
    var more = soon ? ' ' + (soon === 1 ? 'One more chapter is' : soon + ' more chapters are') +
      ' being written, and will show up here.' : '';
    v.nextSlot.innerHTML = '<div class="op-next is-done"><span class="op-next-q">' + esc(line + more) + '</span></div>';
  }
  return nx;
}
// "Clear ticks": lives in the footer, or in the closing box once everything is read.
// Confirmed inline (no confirm() dialog); the safe choice gets the focus.
function paintReset(v, m, done, total) {
  if (!v.resetEl) return;
  var inScope = countIn(v.list, m);
  if (!inScope) v.asking = false;
  var want = !inScope ? 'none' : v.asking ? 'confirm' : 'ask', key = want + '|' + inScope;
  var home = (done === total && total) ? v.nextSlot.querySelector('.op-next.is-done') : v.foot;
  if (home && v.resetEl.parentNode !== home) home.appendChild(v.resetEl);
  if (key === v.resetShown) return;
  v.resetShown = key;
  v.resetEl.hidden = want === 'none';
  v.resetEl.classList.toggle('is-asking', want === 'confirm');
  if (want === 'ask') {
    v.resetEl.innerHTML = '<button type="button" class="op-link" data-op-act="ask">Clear ticks</button>';
  } else if (want === 'confirm') {
    v.resetEl.innerHTML = '<div class="op-ask" role="group" aria-label="Clear ticks">' +
      '<p class="op-ask-q">Clear ' + (inScope === 1 ? 'the 1 tick' : 'all ' + inScope + ' ticks') + '?</p>' +
      '<div class="op-ask-btns"><button type="button" class="op-pill op-pill-strong" data-op-act="clear">Clear ticks</button>' +
      '<button type="button" class="op-pill" data-op-act="keep">Keep them</button></div></div>';
  } else {
    v.resetEl.innerHTML = '';
  }
}
// The switch and the one line under it, which says what is hidden right now.
function paintSeg(v, on, m) {
  if (!v.hideBox) return;
  var hub = v.kind === 'hub', done = countIn(hub ? v.all : v.list, m), st = '', n = 0;
  v.hideBox.hidden = !(on || done > 0);                // nothing to hide until something is read
  v.segBtns[0].setAttribute('aria-pressed', on ? 'false' : 'true');
  v.segBtns[1].setAttribute('aria-pressed', on ? 'true' : 'false');
  if (on && hub) {
    UNITS.forEach(function (u) { if (u.type === 'card' && u.el.classList.contains('op-hid')) n++; });
    st = n ? (n === 1 ? '1 chapter you’ve read is' : n + ' chapters you’ve read are') + ' hidden from the cards below.'
      : 'A chapter’s card hides once you’ve read all of it.';
  } else if (on) {
    v.list.forEach(function (s) { if (LOCAL[s.id] && LOCAL[s.id].classList.contains('op-hid')) n++; });
    st = n ? (n === 1 ? '1 read section is hidden. Tap it in the list to see it again.'
      : n + ' read sections are hidden. Tap one in the list to see it again.')
      : done ? 'Nothing is hidden right now.' : 'Sections hide once you tick “I’ve read this”.';
  }
  if (v.hideSt.textContent !== st) v.hideSt.textContent = st;
  v.hideSt.hidden = !st;
}

function paintView(v, m, on) {
  var total = v.all.length, done = countIn(v.all, m);
  var nx = paintNext(v, m, done, total);
  paintRows(v.el, m, nx);
  paintSeg(v, on, m);
  if (v.isMini) {
    v.count.textContent = done + ' of ' + total + ' read';
    v.line.style.width = (total ? Math.round(done / total * 1000) / 10 : 0) + '%';
    return;
  }
  paintMeter(v.meter, done, total);
  if (v.kind === 'chapter') {
    var cd = countIn(v.list, m);
    v.count.textContent = cd + ' of ' + v.list.length + ' read';
    if (v.allCount) v.allCount.textContent = done + ' of ' + total + ' read in all.';
  } else {
    v.count.textContent = done + ' of ' + total + ' read';
  }
  $all('[data-op-chrow]', v.el).forEach(function (li) {
    var slug = li.getAttribute('data-op-chrow');
    var secs = v.list.filter(function (s) { return s.ch === slug; }), d = countIn(secs, m);
    var c = li.querySelector('.op-ch-c');
    if (c) c.innerHTML = d + '/' + secs.length + '<span class="op-vh"> read</span>';
    li.classList.toggle('is-read', !!secs.length && d === secs.length);
  });
  if (v.note) {
    v.note.textContent = KEEPS
      ? 'Ticks are kept on this device only.'
      : 'This browser isn’t keeping ticks, so they’ll clear when you close the page.';
    v.note.classList.toggle('is-warn', !KEEPS);
  }
  paintReset(v, m, done, total);
}

// The button and the quiet note beside it. The note speaks for the button just tapped
// only; a read section shown again while hiding says so.
var justTapped = null;
function paintButtons(m, on) {
  $all('.op-btn[data-op-id]').forEach(function (b) {
    var id = b.getAttribute('data-op-id'), r = !!m[id];
    b.setAttribute('aria-pressed', r ? 'true' : 'false');
    var t = b.querySelector('.op-btn-t'); if (t) t.textContent = r ? 'Read' : 'I’ve read this';
    var n = b.parentNode.querySelector('.op-done-note'); if (!n) return;
    var txt = !r || pending[id] ? ''
      : id === justTapped && !on ? (KEEPS ? 'Tap again to undo' : 'Tap again to undo. Not kept after you close the page.')
      : on && revealed[id] ? 'Shown for now' : '';
    if (n.textContent !== txt) n.textContent = txt;
  });
}
// Opt-in: links inside [data-op-ticks] (a chapter's own sticky chips, say) get a tick.
function paintTicks(m) {
  $all('[data-op-ticks] a[href*="#"]').forEach(function (a) {
    var id = a.getAttribute('href').split('#')[1];
    if (!SEC[id]) return;
    var t = a.querySelector('.op-tk');
    if (!t) { t = document.createElement('span'); t.className = 'op-tk'; t.innerHTML = ICON + '<span class="op-vh"></span>'; a.appendChild(t); }
    a.classList.toggle('op-is-read', !!m[id]);
    t.querySelector('.op-vh').textContent = m[id] ? ', read' : '';
  });
}

function summary(m) {
  var all = SECTIONS.filter(avail), chs = {}, read = [], nx = null;
  all.forEach(function (s) {
    if (!chs[s.ch]) chs[s.ch] = { done: 0, total: 0 };
    chs[s.ch].total++;
    if (m[s.id]) { chs[s.ch].done++; read.push(s.id); } else if (!nx) nx = s.id;
  });
  return { read: read, done: read.length, total: all.length, next: nx, chapters: chs, keeps: KEEPS, hiding: memHide };
}

// ── HIDE WHAT I'VE READ ──────────────────────────────────────────────────────
// A unit is something the switch can hide: a section on this page, a hub card, or a
// group of cards (a theme). revealed = shown again for this visit (see shownLoad);
// pending = ticked while hiding, waiting out PEND_MS before it folds away.
var UNITS = [], EMPTIES = [], GONES = [], revealed = {}, pending = {}, sayEl = null, sayT = 0;

// "Shown for now" lasts for this tab: sessionStorage, so a reload, or a tab the phone put
// to sleep and reloads, comes back with the same sections showing. Then the browser's own
// scroll restore lands in the same place; Safari has no scroll anchoring to cover for a
// page that came back shorter. Gone when the tab closes, which is what "for this visit"
// means, and cleared whenever the switch is pressed. Keys: a section id, "card:<slug>",
// "group:<element id>". Wrapped: with storage blocked it is memory only, as before.
function shownLoad() {
  var o = {};
  try {
    String(window.sessionStorage.getItem(SKEY) || '').split(',').forEach(function (k) {
      if (/^((card|group):)?[A-Za-z0-9_-]{1,60}$/.test(k)) o[k] = 1;
    });
  } catch (e) {}
  return o;
}
function shownSave() {
  try {
    var a = [];
    for (var k in revealed) if (Object.prototype.hasOwnProperty.call(revealed, k) && revealed[k]) a.push(k);
    if (a.length) window.sessionStorage.setItem(SKEY, a.join(','));
    else window.sessionStorage.removeItem(SKEY);
  } catch (e) {}
}

function collectUnits() {
  var seen = {};
  views.forEach(function (v) {
    if (v.kind === 'hub') return;
    v.list.forEach(function (s) {
      if (!LOCAL[s.id] || seen[s.id]) return;
      seen[s.id] = 1;
      UNITS.push({ key: s.id, el: LOCAL[s.id], type: 'sec', id: s.id, title: s.title });
    });
  });
  $all('[data-op-card]').forEach(function (el) {
    var slug = el.getAttribute('data-op-card');
    if (CH[slug]) UNITS.push({ key: 'card:' + slug, el: el, type: 'card', slug: slug, title: CH[slug].c.title });
  });
  $all('[data-op-cardgroup]').forEach(function (el, i) {
    var cards = UNITS.filter(function (u) { return u.type === 'card' && el.contains(u.el); });
    if (cards.length) UNITS.push({ key: 'group:' + (el.id || i), el: el, type: 'group', cards: cards, title: groupName(el) });
  });
}
// A theme's short name, for the line left where its cards were: data-op-cardgroup="Waves",
// else the text of a link to it (the hub's theme chips), else its heading.
function groupName(el) {
  var n = el.getAttribute('data-op-cardgroup'), a = null, h;
  if (n) return n;
  if (el.id) $all('a[href="#' + el.id + '"]').forEach(function (x) { if (!a && !el.contains(x)) a = x; });
  if (a && a.textContent.replace(/\s+/g, ' ').trim()) return a.textContent.replace(/\s+/g, ' ').trim();
  h = el.querySelector('h2,h3');
  return h ? h.textContent.replace(/\s+/g, ' ').trim() : '';
}
function wantHidden(u, on, m, chs) {
  if (!on || revealed[u.key]) return false;
  if (u.type === 'sec') return !!m[u.id] && !pending[u.id];
  if (u.type === 'card') { var c = chs[u.slug]; return !!c && c.total > 0 && c.done === c.total; }
  for (var i = 0; i < u.cards.length; i++) if (!wantHidden(u.cards[i], on, m, chs)) return false;
  return true;
}
// Returns how many units came back into view (their canvases need a re-measure).
function applyHide(on, m) {
  var chs = summary(m).chapters, shown = 0;
  UNITS.forEach(function (u) {
    var h = wantHidden(u, on, m, chs);
    if (h !== u.el.classList.contains('op-hid')) { u.el.classList.toggle('op-hid', h); if (!h) shown++; }
    if (u.type === 'card') { var c = chs[u.slug]; u.el.classList.toggle('op-is-read', !!c && c.total > 0 && c.done === c.total); }
  });
  document.documentElement.classList.toggle('op-hiding', on);
  EMPTIES.forEach(function (e) { paintEmpty(e, on, m); });
  paintGones(on);
  return shown;
}

// The calm line for when everything here is read and hidden. It sits where the first
// section (or the first card group) was, so the page doesn't look broken.
function buildEmpty(v) {
  var first = null;
  if (v.kind === 'hub') {
    var c = document.querySelector('[data-op-card]');
    first = document.querySelector('[data-op-cardgroup]') || (c && c.parentNode);
  } else {
    v.list.forEach(function (s) {
      var el = LOCAL[s.id];
      if (el && (!first || (first.compareDocumentPosition(el) & 2))) first = el;
    });
  }
  if (!first || !first.parentNode) return;
  var e = document.createElement('div');
  e.className = 'op-empty';
  e.hidden = true;
  first.parentNode.insertBefore(e, first);
  EMPTIES.push({ el: e, v: v, key: null });
}
function emptyAll(e) {
  if (e.v.kind === 'hub') {
    var cards = UNITS.filter(function (u) { return u.type === 'card' && !CH[u.slug].c.soon; });
    if (!cards.length) return false;
    for (var i = 0; i < cards.length; i++) if (!cards[i].el.classList.contains('op-hid')) return false;
    return true;
  }
  for (var j = 0; j < e.v.list.length; j++) {
    var el = LOCAL[e.v.list[j].id];
    if (el && !el.classList.contains('op-hid')) return false;
  }
  return true;
}
function paintEmpty(e, on, m) {
  var v = e.v, show = on && emptyAll(e), nx = show && v.kind === 'chapter' ? nextOf(v, m) : null;
  var key = show ? 'on|' + (nx ? nx.id : '') : 'off';
  if (key === e.key) return;
  e.key = key;
  e.el.hidden = !show;
  if (!show) { e.el.innerHTML = ''; e.el.classList.remove('op-arrive'); return; }
  var line = v.kind === 'hub' ? 'You’ve read every chapter so far, so their cards are hidden.'
    : v.kind === 'chapter' ? (nx ? 'You’ve read this chapter, so its sections are hidden.'
      : 'You’ve read every section so far, so this chapter’s sections are hidden.')
    : 'You’ve read all ' + v.list.length + ' sections, so they’re hidden.';
  e.el.innerHTML = '<p class="op-empty-q">' + esc(line) + '</p>' +
    '<div class="op-ask-btns"><button type="button" class="op-pill op-pill-strong" data-op-act="showall">Show everything</button></div>' +
    (nx ? nextLink(v, nx, 'Next up') : '');
}
function emptyFor(id) {
  for (var i = 0; i < EMPTIES.length; i++) {
    var e = EMPTIES[i];
    if (!e.el.hidden && e.v.kind !== 'hub' && e.v.list.some(function (s) { return s.id === id; })) return e.el;
  }
  return null;
}

// ── SIGNS WHERE THINGS WENT ─────────────────────────────────────────────────
// Hidden things leave one quiet line, so a reader who turned hiding on last week does
// not think part of the page is missing:
//   chapter page: above its first part still showing, "Part 1, which you've read, is
//     hidden. [Show it]" (the switch itself is in the panel at the end of the chapter);
//   hub: in each theme, where its hidden cards were, "Waves: 4 chapters you've read are
//     hidden. [Show them]"; when the whole theme is hidden the line stands in its place,
//     and the theme's chip (a link to it) gets a tick.
// A line shows only while something it speaks for is hidden. Once the calm card shows
// (every part of a chapter, or every chapter on the hub, hidden) it says it instead, so
// no line repeats it; only a hub theme still on screen keeps its own. Show it / Show
// them shows those for this visit, like a tap in the contents.
function buildGones() {
  views.forEach(function (v) {
    if (v.kind !== 'chapter' || !v.hideBox) return;
    var parts = [];
    v.list.forEach(function (s) { if (LOCAL[s.id]) parts.push(LOCAL[s.id]); });
    parts.sort(function (a, b) { return (a.compareDocumentPosition(b) & 4) ? -1 : 1; });
    var us = parts.map(unitOf).filter(Boolean);
    if (!us.length || !parts[0].parentNode) return;
    var e = document.createElement('div');
    e.className = 'op-gone';
    e.hidden = true;
    parts[0].parentNode.insertBefore(e, parts[0]);
    GONES.push({ el: e, type: 'parts', units: us, key: null });
  });
  UNITS.forEach(function (u) {
    if (u.type !== 'group') return;
    var e = document.createElement('div'), chips = [];
    e.className = 'op-gone op-gone-group';
    e.hidden = true;
    if (u.el.id) $all('a[href="#' + u.el.id + '"]').forEach(function (a) { if (!u.el.contains(a)) chips.push(a); });
    GONES.push({ el: e, type: 'group', u: u, box: u.cards[0].el.parentNode, chips: chips, key: null });
  });
}
function listWords(a) {                                 // [1, 2, 4] -> "1, 2 and 4"
  return a.length < 2 ? String(a[0]) : a.slice(0, -1).join(', ') + ' and ' + a[a.length - 1];
}
function paintGones(on) {
  GONES.forEach(function (g) {
    var txt = '', more = '', n = 0, home = null, before = null, i;
    if (g.type === 'parts') {
      var nums = [], names = [];
      g.units.forEach(function (u, k) { if (u.el.classList.contains('op-hid')) { nums.push(k + 1); names.push(u.title); } });
      n = nums.length;
      if (on && n && n < g.units.length) {
        txt = (n === 1 ? 'Part ' : 'Parts ') + listWords(nums) + ', which you’ve read, ' + (n === 1 ? 'is' : 'are') + ' hidden.';
        more = n === 1 ? 'Part ' + nums[0] + ', ' + names[0] : 'Parts ' + listWords(nums);
      }
    } else {
      var u = g.u, gone = u.el.classList.contains('op-hid'), titles = [], done = true;
      u.cards.forEach(function (c) {
        if (c.el.classList.contains('op-hid')) titles.push(c.title);
        if (!c.el.classList.contains('op-is-read')) done = false;
      });
      n = titles.length;
      // With every chapter read the calm card stands where the themes were and says it,
      // so a theme that has gone altogether leaves no second line under it; a theme that
      // is still showing (beside a chapter being written, say) keeps its line.
      if (on && n && !(gone && EMPTIES.some(function (e) { return e.v.kind === 'hub' && !e.el.hidden; }))) {
        txt = (u.title ? u.title + ': ' : '') + (n === 1 ? '1 chapter you’ve read is hidden.' : n + ' chapters you’ve read are hidden.');
        more = n === 1 ? titles[0] : 'the ' + n + ' chapters';
        if (gone) { home = u.el.parentNode; before = u.el; } else { home = g.box; before = null; }
      }
      for (i = 0; i < g.chips.length; i++) {             // the theme's chip: a tick once it is all read
        var a = g.chips[i], t = a.querySelector('.op-tk');
        if (!t) { t = document.createElement('span'); t.className = 'op-tk'; t.innerHTML = ICON + '<span class="op-vh"></span>'; a.appendChild(t); }
        a.classList.toggle('op-is-read', done);
        var sv = done ? (gone ? ', read, hidden' : ', read') : '';
        if (t.lastChild.textContent !== sv) t.lastChild.textContent = sv;
      }
    }
    if (home && (g.el.parentNode !== home || g.el.nextSibling !== before)) home.insertBefore(g.el, before);
    var key = txt ? txt + '|' + (before ? 'b' : 'a') : '';
    if (key === g.key) return;
    g.key = key;
    g.el.hidden = !txt;
    g.el.innerHTML = txt ? '<p class="op-gone-q">' + esc(txt) + ' <button type="button" class="op-link op-gone-b" data-op-act="showgone">' +
      (n === 1 ? 'Show it' : 'Show them') + '<span class="op-vh">: ' + esc(more) + '</span></button></p>' : '';
  });
}
// Show it / Show them: what comes back lands where the line was, and gets the focus.
function showGone(g) {
  var y0 = g.el.getBoundingClientRect().top, us, first = null;
  if (g.type === 'parts') us = g.units.filter(function (u) { return u.el.classList.contains('op-hid'); });
  else us = [g.u];
  if (!us.length) return;
  if (g.type === 'parts') first = us[0].el;
  else if (g.u.el.classList.contains('op-hid')) first = g.u.el;
  else g.u.cards.forEach(function (c) { if (!first && c.el.classList.contains('op-hid')) first = c.el; });
  holdAnchor();
  reveal(us);
  if (first && rendered(first)) {
    var land = Math.max(y0, topLine() + LAND);
    window.scrollBy(0, first.getBoundingClientRect().top - land);
    var f = first.tagName === 'SECTION' ? first.querySelector('h2,h3') : null;
    if (!f) { var card = first.hasAttribute('data-op-card') ? first : first.querySelector('[data-op-card]'); f = card && card.querySelector('a[href]'); }
    softFocus(f || first);
  }
  releaseAnchor();
}

// One polite voice for "Hidden: …" / "Shown: …". Cleared first, so the same words twice
// are still read out.
function say(t) {
  if (!sayEl) return;
  clearTimeout(sayT);
  sayEl.textContent = '';
  sayT = setTimeout(function () { sayEl.textContent = t; }, 80);
}

// ── KEEPING YOUR PLACE ───────────────────────────────────────────────────────
// Hiding or showing things above where you're reading would shift the page under you
// (Safari has no scroll anchoring). keepPlace(fn) notes what sits at the top of the
// screen, runs fn, then scrolls so it is still there. If that very spot was hidden,
// the next thing still showing moves up to take its place.
var holds = 0;
function holdAnchor() { holds++; document.documentElement.classList.add('op-noanchor'); }
function releaseAnchor() {
  raf(function () { raf(function () {
    holds = Math.max(0, holds - 1);
    if (!holds) document.documentElement.classList.remove('op-noanchor');
  }); });
}
// The bottom of whatever is pinned to the top of the screen: the slim bar, or a
// chapter page's sticky contents. Things brought up to the top land LAND px below it,
// where an anchor jump lands too (scroll-padding 60 + the sections' 16 px margin).
// Inside the installed iPhone app (viewport-fit=cover, translucent status bar) the top
// ~47 px belong to the status bar: env(safe-area-inset-top), measured by a probe, so a
// bar that sits below it (top: env(...)) is still found.
var LAND = 16, insetEl = null;
function safeTop() { return insetEl ? insetEl.offsetHeight : 0; }
function topLine() {
  if (mini && mini.el.classList.contains('is-on')) return mini.el.firstChild.getBoundingClientRect().bottom;
  var x = Math.round(document.documentElement.clientWidth / 2), e = null, y = 0, s = safeTop();
  try { e = document.elementFromPoint(x, s + 2); } catch (er) {}
  for (; e && e.nodeType === 1 && e !== document.body; e = e.parentNode) {
    var cs = window.getComputedStyle(e), p = cs.position;
    if (p === 'fixed' || p === 'sticky') {
      var r = e.getBoundingClientRect();
      if (r.top <= Math.max(s, parseFloat(cs.top) || 0) + 2) y = r.bottom;
      break;
    }
  }
  return y;
}
// What is at height y. The slim bar's own drop-down is looked through. Landing in a
// gap between blocks picks the next block down.
function probe(y) {
  var mb = mini && mini.el, vis = '', e = null;
  if (mb) { vis = mb.style.visibility; mb.style.visibility = 'hidden'; }
  try { e = document.elementFromPoint(Math.round(document.documentElement.clientWidth / 2), y); } catch (er) {}
  if (mb) mb.style.visibility = vis;
  if (e && e.children && e.children.length) {
    var inKid = false, after = null;
    for (var i = 0; i < e.children.length; i++) {
      var k = e.children[i]; if (!rendered(k)) continue;
      var r = k.getBoundingClientRect();
      if (r.top <= y && r.bottom > y) { inKid = true; break; }
      if (!after && r.top > y) after = k;
    }
    if (!inKid && after) e = after;
  }
  return e;
}
function nextVisibleAfter(el) {
  for (var n = el; n && n !== document.body; n = n.parentNode) {
    for (var s = n.nextElementSibling; s; s = s.nextElementSibling) {
      if (/^(SCRIPT|STYLE|TEMPLATE|LINK)$/.test(s.tagName) || !rendered(s) || s.classList.contains('op-spacer')) continue;
      var p = window.getComputedStyle(s).position;
      if (p === 'fixed' || p === 'absolute') continue;
      return s;
    }
  }
  return null;
}
function keepPlace(fn) {
  var y = topLine(), a = probe(y + 1), chain = [], tops = [], i;
  for (var e = a; e && e.nodeType === 1 && e !== document.body && e !== document.documentElement; e = e.parentNode) {
    chain.push(e); tops.push(e.getBoundingClientRect().top);
  }
  holdAnchor();
  fn();
  var gone = -1;
  for (i = 0; i < chain.length; i++) if (chain[i].classList.contains('op-hid')) gone = i;
  if (gone > -1 && tops[gone] <= y + 1) {
    var nx = nextVisibleAfter(chain[gone]);
    if (nx) window.scrollBy(0, nx.getBoundingClientRect().top - (y + LAND));
  } else {
    for (i = 0; i < chain.length; i++) {
      if (!rendered(chain[i]) || chain[i].classList.contains('op-hid')) continue;
      var dy = chain[i].getBoundingClientRect().top - tops[i];
      if (Math.abs(dy) > 0.5) window.scrollBy(0, dy);
      break;
    }
  }
  releaseAnchor();
}

// ── Showing a hidden section again ──────────────────────────────────────────
function unitsAround(el) {                              // every unit that holds el (or is el)
  return UNITS.filter(function (u) { return u.el === el || u.el.contains(el); });
}
function reveal(us, quiet) {
  var names = [], any = false;
  us.forEach(function (u) {
    (u.type === 'group' ? [u].concat(u.cards) : [u]).forEach(function (x) {
      if (x.el.classList.contains('op-hid') && !revealed[x.key]) { any = true; if (x.type !== 'group') names.push(x.title); }
      revealed[x.key] = 1;
    });
  });
  shownSave();
  if (!any) return false;
  update();
  if (!quiet && names.length) say('Shown: ' + names.join(', ') + '.');
  return true;
}
function unitOf(el) { for (var i = 0; i < UNITS.length; i++) if (UNITS[i].el === el) return UNITS[i]; return null; }
function revealFor(el) {
  return reveal(unitsAround(el).filter(function (u) { return u.el.classList.contains('op-hid'); }));
}
// The element a hash points at. A hash of settings ("#h=1.5&t=12…", a shared simulator
// link) opens the simulator, so it is the simulator's section (paramsHash) that matters.
function hashTarget(h) {
  if (!h) return null;
  if (h.indexOf('=') > -1) return document.getElementById(CFG.paramsHash || 'sim');
  var id = h;
  try { id = decodeURIComponent(h); } catch (e) {}
  try { return document.getElementById(id); } catch (e) { return null; }
}
function linkTarget(a) {                                // only links into this same page
  var href = a.getAttribute('href') || '', i = href.indexOf('#');
  if (i < 0) return null;
  if (i > 0 && (a.pathname !== location.pathname || a.host !== location.host)) return null;
  return hashTarget(href.slice(i + 1));
}

// ── Ticking a section while hiding: Undo, then fold away ────────────────────
// p.at is where its button sat on the screen at the tap: by the time the section goes,
// the reader may have scrolled on into the next one, and then nothing they are reading
// may move. (Not scrollY: the kit's own place-keeping changes scrollY whenever the
// contents above change height, and a tap does that: the next-up question changes.)
function btnTop(id) {
  var b = LOCAL[id] && LOCAL[id].querySelector('.op-btn');
  return b && rendered(b) ? b.getBoundingClientRect().top : null;
}
function startPending(id) {
  cancelPending(id);
  if (revealed[id]) { delete revealed[id]; shownSave(); }
  var row = LOCAL[id] && LOCAL[id].querySelector('.op-done');
  var p = pending[id] = { t: 0, box: row && row.querySelector('.op-pend'), at: btnTop(id) };
  if (p.box) p.box.hidden = false;
  // The visible "Hiding it in a moment." is not a live region: say it once, here.
  armPending(id, PEND_MS);
}
function armPending(id, ms) {
  var p = pending[id]; if (!p) return;
  clearTimeout(p.t);
  p.t = setTimeout(function () { collapse(id); }, ms);
}
function holdPending(id) { var p = pending[id]; if (p) { clearTimeout(p.t); p.t = 0; } }
function cancelPending(id) {
  var p = pending[id]; if (!p) return;
  clearTimeout(p.t);
  if (p.box) p.box.hidden = true;
  delete pending[id];
}
function viewH() { return window.innerHeight || document.documentElement.clientHeight; }
function collapse(id) {
  var p = pending[id]; if (!p) return;
  var u = null;
  UNITS.forEach(function (x) { if (x.key === id) u = x; });
  if (!u || !load()[id] || !hideOn()) { cancelPending(id); update(); return; }
  var now = btnTop(id), dy = now == null || p.at == null ? 9 : p.at - now;   // > 0: scrolled on
  var scrolled = Math.abs(dy) > 2, vh = viewH();
  var r = u.el.getBoundingClientRect(), onScreen = r.bottom > topLine() && r.top < vh;
  // Scrolled back up to it (to read it again, say), or it still runs off the bottom of
  // the screen: it waits until it is out of the way, rather than swap what you are
  // reading for something else. Checked again every 2.5 s; Undo and the switch still work.
  if (scrolled && onScreen && (dy < 0 || r.bottom >= vh - 1)) { armPending(id, 2500); return; }
  // Not cancelPending: its "Hiding it in a moment" row must go in the same step as the
  // section, inside the place-keeping, or that row's own height (about 50 px on a phone)
  // moves whatever is below it first.
  clearTimeout(p.t);
  delete pending[id];
  foldAway(u, u.el.contains(document.activeElement), scrolled, p.box);
}
// How a ticked section goes:
// - still at its button (no scroll since the tap, button on screen): it fades, then the
//   next thing slides up into the room it leaves, so you see where it went;
// - scrolled on since the tap, or the button is off screen: whatever you are reading
//   stays exactly where it is. The first block below the section that is on screen
//   keeps its place (holdStill); only what was above it changes;
// - off screen altogether: it simply goes, and the page keeps your place;
// - scrolled back up to it: it waits (see collapse).
// Reduced motion: no fade and no slide, the same places.
function foldAway(u, hadFocus, scrolled, box) {
  var sec = u.el, y = topLine(), r = sec.getBoundingClientRect(), vh = viewH();
  function change() { if (box) box.hidden = true; update(); }
  var btn = sec.querySelector('.op-btn'), br = btn && rendered(btn) ? btn.getBoundingClientRect() : null;
  var atButton = !scrolled && !!br && br.bottom > y && br.top < vh;
  function after() {
    if (hadFocus) {
      var em = emptyFor(u.id), n = em || nextVisibleAfter(sec);
      softFocus(em || (n && (n.querySelector('h2,h3') || n)));
    }
    say('Hidden: ' + u.title + '.');
  }
  if (r.bottom <= y || r.top >= vh) { keepPlace(change); after(); return; }
  if (!atButton) {
    if (reduced()) { holdStill(sec, change); after(); return; }
    holdAnchor();
    sec.classList.add('op-fading');
    setTimeout(function () {
      holdStill(sec, change);
      sec.classList.remove('op-fading');
      after(); releaseAnchor();
    }, 220);
    return;
  }
  if (reduced()) { keepPlace(change); after(); return; }
  holdAnchor();
  sec.classList.add('op-fading');
  setTimeout(function () {
    var b = nextVisibleAfter(sec), tl = topLine(), st = sec.getBoundingClientRect().top;
    var aTop = st >= tl ? st : tl + LAND;               // where the next thing should end up
    var bTop0 = b ? b.getBoundingClientRect().top : 0;
    var was = GONES.map(function (g) { return !g.el.hidden; });
    change();
    sec.classList.remove('op-fading');
    var em = emptyFor(u.id);
    if (em) {                                           // that was the last one: the calm line
      window.scrollBy(0, em.getBoundingClientRect().top - aTop);
      em.classList.add('op-arrive');
      after(); releaseAnchor(); return;
    }
    if (!b || !rendered(b)) { after(); releaseAnchor(); return; }
    // A sign line that has just appeared in the section's place ("Part 1, which you've
    // read, is hidden") leads: it lands at aTop and the next thing follows it up.
    var lead = b;
    GONES.forEach(function (g, i) { if (!was[i] && !g.el.hidden && rendered(g.el) && nextVisibleAfter(g.el) === b) lead = g.el; });
    window.scrollBy(0, lead.getBoundingClientRect().top - aTop);   // where it will end up
    var gap = bTop0 - b.getBoundingClientRect().top;               // ...and how far below that b still is
    if (gap < 2) { after(); releaseAnchor(); return; }
    // A spacer holds it where it was, then closes. Its start height goes in with
    // transitions off, or the browser would animate up to it and straight back down.
    var sp = document.createElement('div'), ended = false;
    sp.className = 'op-spacer';
    sp.setAttribute('aria-hidden', 'true');
    sp.style.transition = 'none';
    sp.style.height = gap + 'px';
    lead.parentNode.insertBefore(sp, lead);
    reflow(sp);
    sp.style.transition = '';
    sp.style.height = '0px';
    function end() {
      if (ended) return;
      ended = true;
      if (sp.parentNode) sp.parentNode.removeChild(sp);
      releaseAnchor();
    }
    sp.addEventListener('transitionend', end);
    setTimeout(end, 700);
    after();
  }, 220);
}
// Hide sec without moving what you are reading: the first block below it that is on
// screen keeps its top where it is. (If nothing below it is on screen, keepPlace does
// the same from the top line.)
function holdStill(sec, change) {
  var b = nextVisibleAfter(sec), t0;
  change = change || update;
  if (!b || (t0 = b.getBoundingClientRect().top) >= viewH()) { keepPlace(change); return; }
  holdAnchor();
  change();
  if (rendered(b)) {
    var dy = b.getBoundingClientRect().top - t0;
    if (Math.abs(dy) > 0.5) window.scrollBy(0, dy);
  }
  releaseAnchor();
}

// The switch itself. Turning it on hides everything read, including anything shown
// again for this visit; turning it off shows everything and drops any pending fold.
function setHiding(on) {
  on = !!on;
  if (on === hideOn() && !on) return;
  for (var id in pending) if (Object.prototype.hasOwnProperty.call(pending, id)) cancelPending(id);
  revealed = {};
  shownSave();
  saveHide(on);
  keepPlace(update);
  if (!on) { say('Shown: everything.'); return; }
  var s = 0, c = 0;
  UNITS.forEach(function (u) {
    if (!u.el.classList.contains('op-hid')) return;
    if (u.type === 'sec') s++; else if (u.type === 'card') c++;
  });
  say(s ? 'Hidden: ' + s + (s === 1 ? ' read section.' : ' read sections.')
    : c ? 'Hidden: ' + c + (c === 1 ? ' chapter you’ve read.' : ' chapters you’ve read.')
    : 'Read sections will hide as you tick them.');
}

// What a tap on a read button says: the section, the new count, and, while hiding, the
// Undo window. One message, so two live regions never talk over each other.
function tapSay(id, on, pend) {
  if (!SEC[id]) return;
  var d = summary(load());
  say((on ? 'Read: ' : 'Not read: ') + SEC[id].title + ', ' + d.done + ' of ' + d.total + '.' +
    (pend ? ' It will hide in a few seconds; Undo is next.' : ''));
}
// ocean.js's "Next · chapter N" card at the foot of a chapter page: rebuilt from the kit,
// so it names the first chapter after this one with something unread and asks that
// section's registry question, the same one the kit asks. When the calm card is already
// asking it, the card keeps only the chapter name.
function paintPn() {
  var a = document.querySelector('.pn-next'), v = null;
  views.forEach(function (x) { if (x.kind === 'chapter' && !v) v = x; });
  if (!a || !v) return;
  var nx = nextOf(v, load());
  if (!nx || nx.ch === v.ch || !CH[nx.ch]) return;   // nothing unread elsewhere: ocean.js's own card stands
  var k = a.querySelector('.pn-k'), t = a.querySelector('.pn-t'), h = a.querySelector('.pn-h'), q = qFor(nx);
  a.href = hrefFor(nx, 'chapter');
  if (k) k.textContent = 'Next · chapter ' + CH[nx.ch].n + ' →';
  if (t) t.textContent = CH[nx.ch].c.title;
  if (h) { h.textContent = q || ''; h.hidden = !q || calmNext(v); }
}

// ── UPDATE ───────────────────────────────────────────────────────────────────
function update() {
  var m = load(), on = hideOn();
  var shown = applyHide(on, m);
  paintButtons(m, on);
  views.forEach(function (v) { paintView(v, m, on); });
  if (mini) paintView(mini, m, on);
  paintTicks(m);
  paintPn();
  if (shown) fireResize();                              // canvases that were display:none re-measure
  try {
    var d = summary(m), ev;
    try { ev = new CustomEvent('oceanprogress', { detail: d }); }
    catch (e) { ev = document.createEvent('CustomEvent'); ev.initCustomEvent('oceanprogress', false, false, d); }
    document.dispatchEvent(ev);
  } catch (e) {}
}
function refresh() { keepPlace(update); }

// ── ACTIONS ──────────────────────────────────────────────────────────────────
function setRead(id, on) {
  if (!SEC[id]) return;
  var m = load();                                       // fresh: another tab may have written
  if (on) m[id] = 1; else delete m[id];
  save(m);
  refresh();
  if (on) logRead(id);
}
function clearIds(ids) {
  var m = load();
  ids.forEach(function (id) { delete m[id]; cancelPending(id); });
  save(m);
  refresh();
}

function addButtons() {
  views.forEach(function (v) {
    if (v.kind === 'hub') return;
    v.list.forEach(function (s) {
      var sec = LOCAL[s.id];
      if (!sec || sec.querySelector('.op-btn')) return;
      var host = sec.querySelector('[data-op-read]') || sec;
      var row = document.createElement('div');
      row.className = 'op-done';
      // One name in both states ("I've read this: Sets"); aria-pressed carries the state,
      // as the toggle-button pattern asks. The visible "Read" stays inside that name, so
      // voice control ("tap Read") and WCAG 2.5.3 (label in name) still match.
      row.innerHTML = '<button type="button" class="op-btn" aria-pressed="false" data-op-id="' + esc(s.id) + '"' +
        ' aria-label="' + esc('I’ve read this: ' + s.title) + '">' +
        ICON + '<span class="op-btn-t">I’ve read this</span></button>' +
        '<span class="op-done-note" aria-hidden="true"></span>' +
        '<span class="op-pend" hidden><span class="op-pend-t">Hiding it in a moment.</span>' +
        '<button type="button" class="op-pill op-undo" data-op-act="undo" data-op-id="' + esc(s.id) + '">Undo' +
        '<span class="op-vh"> – keep ' + esc(s.title) + ' showing and not ticked</span></button></span>';
      host.appendChild(row);
    });
  });
}

// ── WHERE AM I (page views): the section in view is marked in the lists and named in
// the slim bar, and the bar shows once the contents panel has scrolled away. Hidden
// sections are skipped. ──────────────────────────────────────────────────────────
var here = null;
function onScroll() {
  var pv = null;
  for (var i = 0; i < views.length; i++) if (views[i].kind !== 'hub' && views[i].list.length) { pv = views[i]; break; }
  if (!pv) return;
  // the reading line: 40 px under the slim bar (measured: it is taller in the installed
  // iPhone app, where the status bar sits on top of it) or under a sticky contents bar
  var line = (mini ? mini.el.firstChild.offsetHeight : topLine()) + 40, cur = null;
  for (var j = 0; j < pv.list.length; j++) {
    var el = LOCAL[pv.list[j].id];
    if (el && rendered(el) && el.getBoundingClientRect().top - line <= 0) cur = pv.list[j];
  }
  var id = cur ? cur.id : null;
  if (id !== here) {
    here = id;
    $all('[data-op-row] > a').forEach(function (a) {
      if (id && a.parentNode.getAttribute('data-op-row') === id && a.getAttribute('href') === '#' + id) a.setAttribute('aria-current', 'location');
      else a.removeAttribute('aria-current');
    });
    if (mini) {
      var k = cur ? pv.list.indexOf(cur) : -1;
      mini.where.innerHTML = cur
        ? (pv.kind === 'page' ? '<span class="op-mini-n">' + pad(k + 1) + '</span>' : '') + esc(cur.title)
        : 'Contents';
    }
  }
  if (mini) {
    var r = mini.src.root.getBoundingClientRect(), show = r.bottom < 0;
    if (show !== mini.el.classList.contains('is-on')) {
      mini.el.classList.toggle('is-on', show);
      if (!show && mini.open) setMini(mini, false, false);
    }
  }
}
var queued = false;
function queue() { if (!queued) { queued = true; raf(function () { queued = false; onScroll(); }); } }

var moved = false;                                      // has the reader scrolled by hand yet?
function bind() {
  document.addEventListener('click', function (e) {
    var t = e.target, b;
    if ((b = closest(t, '.op-btn'))) {
      var id = b.getAttribute('data-op-id'), on = b.getAttribute('aria-pressed') !== 'true';
      justTapped = id;
      if (on && hideOn() && LOCAL[id]) startPending(id);   // stays put, with Undo, for PEND_MS
      if (!on) cancelPending(id);
      setRead(id, on);
      tapSay(id, on, !!pending[id]);
      return;
    }
    if ((b = closest(t, '[data-op-hide]'))) { setHiding(b.getAttribute('data-op-hide') === '1'); return; }
    if ((b = closest(t, '[data-op-act]'))) {
      var v = null, act = b.getAttribute('data-op-act');
      if (act === 'undo') {
        var uid = b.getAttribute('data-op-id');
        cancelPending(uid);
        justTapped = null;
        setRead(uid, false);
        if (LOCAL[uid]) softFocus(LOCAL[uid].querySelector('.op-btn'));
        return;
      }
      if (act === 'showall') {
        var em = null;
        EMPTIES.forEach(function (x) { if (x.el.contains(b)) em = x; });
        setHiding(false);
        if (em) {                                       // its button is gone: focus where the reading starts
          var f = null;
          if (em.v.kind === 'hub') f = document.querySelector('[data-op-cardgroup] h2, [data-op-cardgroup] h3, [data-op-card] h3, [data-op-card] a');
          else em.v.list.forEach(function (s) { var x = LOCAL[s.id]; if (x && (!f || (f.compareDocumentPosition(x) & 2))) f = x; });
          if (f && f.tagName === 'SECTION') f = f.querySelector('h2,h3') || f;
          softFocus(f);
        }
        return;
      }
      if (act === 'showgone') {
        var g = null;
        GONES.forEach(function (x) { if (x.el.contains(b)) g = x; });
        if (g) showGone(g);
        return;
      }
      views.forEach(function (x) { if (x.el.contains(b)) v = x; });
      if (!v) return;
      if (act === 'ask') { v.asking = true; update(); focusIn(v.resetEl, '[data-op-act="keep"]'); }
      else if (act === 'keep') { v.asking = false; update(); focusIn(v.resetEl, '[data-op-act="ask"]'); }
      else if (act === 'clear') {
        v.asking = false;
        clearIds(v.list.map(function (s) { return s.id; }));
        focusIn(v.nextSlot, '.op-next');
      }
      return;
    }
    // Any link into this page whose target is hidden: show it first, then let the
    // browser scroll to it as usual.
    var a = closest(t, 'a[href]');
    if (a && !e.defaultPrevented && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey && UNITS.length) {
      var tg = linkTarget(a);
      if (tg) revealFor(tg);
    }
    if (mini) {
      if ((b = closest(t, '.op-mini-btn'))) { setMini(mini, !mini.open, false); return; }
      if (mini.open && (closest(t, '.op-mini-panel a') || !closest(t, '.op-mini'))) setMini(mini, false, false);
    }
  });
  document.addEventListener('keydown', function (e) {
    if (mini && mini.open && (e.key === 'Escape' || e.key === 'Esc')) setMini(mini, false, true);
  });
  // Undo stays put while a pointer rests on it or it has keyboard focus.
  function pend(e) { var p = closest(e.target, '.op-pend'), u = p && p.querySelector('.op-undo'); return u ? u.getAttribute('data-op-id') : null; }
  document.addEventListener('mouseover', function (e) { var id = pend(e); if (id) holdPending(id); });
  document.addEventListener('mouseout', function (e) {
    var id = pend(e); if (id && !(e.relatedTarget && closest(e.relatedTarget, '.op-pend'))) armPending(id, 3000);
  });
  document.addEventListener('focusin', function (e) { if (closest(e.target, '.op-undo')) { var id = pend(e); if (id) holdPending(id); } });
  document.addEventListener('focusout', function (e) { if (closest(e.target, '.op-undo')) { var id = pend(e); if (id) armPending(id, 3000); } });
  // a hash typed in, or Back/Forward to one
  window.addEventListener('hashchange', function () {
    var tg = hashTarget(location.hash.slice(1));
    if (tg && UNITS.length && revealFor(tg)) { try { tg.scrollIntoView(); } catch (e) {} }
  });
  // other tabs (storage fires only there), the back button (bfcache), coming back to
  // the tab (covers a cookie-only browser, where no storage event fires)
  window.addEventListener('storage', function (e) { if (!e.key || e.key === KEY || e.key === HKEY) refresh(); });
  window.addEventListener('pageshow', function (e) { if (e.persisted) refresh(); });
  document.addEventListener('visibilitychange', function () { if (!document.hidden) refresh(); });
  window.addEventListener('scroll', queue, { passive: true });
  window.addEventListener('resize', queue);
  ['wheel', 'touchmove', 'keydown'].forEach(function (n) { window.addEventListener(n, function () { moved = true; }, { passive: true, capture: true }); });
}
function focusIn(root, sel) {
  var el = root && root.querySelector(sel);
  if (el) { try { el.focus(); } catch (e) {} }
}

// ── INIT ─────────────────────────────────────────────────────────────────────
function init() {
  var els = $all('[data-op-contents]');
  if (!els.length) return;
  els.forEach(function (el) {
    var kind = el.getAttribute('data-op-contents') || 'page';
    if (kind === 'page') {
      SECTIONS.forEach(function (s) {
        var sec = document.getElementById(s.id);
        if (sec && sec.tagName === 'SECTION') LOCAL[s.id] = sec;
      });
    } else if (kind === 'chapter') {
      var slug = el.getAttribute('data-op-chapter');
      SECTIONS.forEach(function (s) {
        var sec = s.ch === slug && document.getElementById(s.id);
        if (sec && sec.tagName === 'SECTION') LOCAL[s.id] = sec;
      });
    }
  });
  els.forEach(function (el) {
    var kind = el.getAttribute('data-op-contents') || 'page';
    if (kind !== 'page' && kind !== 'hub' && kind !== 'chapter') return;
    var v = buildView(el, kind, el.getAttribute('data-op-chapter'));
    if (!v) return;
    views.push(v);
    var want = el.getAttribute('data-op-mini');
    if (!mini && (want === 'on' || (kind === 'page' && want !== 'off')) && v.list.length) mini = buildMini(v);
  });
  addButtons();
  collectUnits();
  views.forEach(function (v) {
    if (v.hideBox && !EMPTIES.some(function (e) { return e.v.kind === v.kind; })) buildEmpty(v);
  });
  buildGones();
  sayEl = document.createElement('div');
  sayEl.className = 'op-vh';
  sayEl.setAttribute('aria-live', 'polite');
  sayEl.setAttribute('data-op-say', '');
  document.body.appendChild(sayEl);
  insetEl = document.createElement('div');              // measures env(safe-area-inset-top)
  insetEl.setAttribute('aria-hidden', 'true');
  insetEl.style.cssText = 'position:fixed;top:0;left:0;width:0;height:0;height:env(safe-area-inset-top,0px);visibility:hidden;pointer-events:none';
  document.body.appendChild(insetEl);
  bind();
  // What was shown again earlier in this tab comes back first, so the page has the
  // layout it had before a reload. Then a link to a hidden section (or a shared
  // simulator link) shows that one too.
  revealed = shownLoad();
  var tg = hashTarget(location.hash.slice(1));
  if (tg) unitsAround(tg).forEach(function (u) {
    revealed[u.key] = 1;
    if (u.type === 'group') u.cards.forEach(function (c) { revealed[c.key] = 1; });
  });
  shownSave();
  update();
  onScroll();
  // On a fresh visit to that link, put it back at the top of the screen once the read
  // ones above it are gone. Never on a reload or Back/Forward: there the browser puts
  // you back where you were, and the layout above already matches.
  if (tg && freshVisit() && hideOn() && document.querySelector('.op-hid')) {
    var land = function () { if (!moved) { try { tg.scrollIntoView(); } catch (e) {} } };
    raf(land);
    window.addEventListener('load', land);
  }
}
function freshVisit() {
  try {
    var nt = performance.getEntriesByType && performance.getEntriesByType('navigation')[0];
    if (nt && nt.type) return nt.type === 'navigate' || nt.type === 'prerender';
    if (performance.navigation) return performance.navigation.type === 0;   // older Safari
  } catch (e) {}
  return true;
}

window.OceanProgress = {
  KEY: KEY,
  HIDE_KEY: HKEY,
  registry: REG,
  keeps: KEEPS,                                         // false: this browser won't keep ticks
  isRead: function (id) { return !!load()[id]; },
  setRead: setRead,
  summary: function () { return summary(load()); },
  chapter: function (slug) { return summary(load()).chapters[slug] || { done: 0, total: 0 }; },
  clear: function (ids) { clearIds(ids || SECTIONS.map(function (s) { return s.id; })); },
  hiding: function () { return hideOn(); },
  setHiding: setHiding,
  reveal: function (id) { var el = document.getElementById(id); return el ? revealFor(el) : false; },
  refresh: refresh
};
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
})();
