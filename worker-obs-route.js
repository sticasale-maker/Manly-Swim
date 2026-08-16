// ─────────────────────────────────────────────────────────────────────────────
// /obs route for the bold-rain-6ded Worker — BOM North Head real-time wind.
//
// WHY A PROXY AT ALL: bom.gov.au returns NO Access-Control-Allow-Origin header,
// so the browser cannot read it directly (verified 12 Aug 2026 — the response
// carries only Content-Type). BOM also 403s a request with no/So default UA, so
// the fetch must send a browser User-Agent. Both are handled here, once.
//
// Follows the same conventions as the /tune route, which were read off the
// ACTUAL deployed source (worker.patched.js):
//   • KV binding  → env.RATE     (also holds ns:session, rate caps, tune overrides)
//   • helpers     → CORS, JSONH(), errJSON() already exist in the file.
//
// PASTE IN THE DASHBOARD EDITOR, NOT WRANGLER: this Worker is dashboard-managed;
// deploying from a local copy has stripped its KV binding and cron triggers before.
//
// ── HOW TO APPLY THIS (checked against the deployed source, 16 Aug 2026) ────
// /obs is ALREADY deployed and its routing line is ALREADY in the fetch handler:
//     if (url.pathname === "/obs") return handleObs(request, env, url, ctx);
// Do not add it again. The only thing that changes is the block below.
//
// worker-obs-PASTE.js in this folder is exactly that block and nothing else —
// open it, select all, copy. In the dashboard editor select from this line:
//     // Station whitelist. Keys MUST match OBS_STATIONS in index.html. Anything not
// down to the closing brace of handleObs — that is the last `}` on its own line
// before:
//     var worker_patched_default = {
// Delete the selection, paste, save. One selection, one paste, nothing to edit
// by hand afterwards.
//
// NEVER paste a fragment of this. The deployed file is an ES module (it ends in
// `export { worker_patched_default as default }`), so it runs in strict mode and
// a stray assignment to an undeclared name — `OBS_HIST_MAX_H = 36` without its
// `var` — throws a ReferenceError while the module is being evaluated. That does
// not break the /obs route; it stops the Worker starting AT ALL, so /forecast
// dies too and the Forecast App with it. Whole block or nothing.
//
// TWO CHANGES COME WITH THIS PASTE, not one. The deployed handleObs predates the
// UA-fallback work of 13 Aug: it makes a single BOM call with one User-Agent and
// no per-UA cacheKey. The block below has the two-attempt version. That is the
// intended state — the deployed copy is simply behind — but it means the request
// to BOM changes shape at the same time as `hourly` arrives, so if something
// looks wrong afterwards, those are the two suspects.
// ─────────────────────────────────────────────────────────────────────────────

// Station whitelist. Keys MUST match OBS_STATIONS in index.html. Anything not
// listed is refused — this route must never become an open proxy to bom.gov.au.
//
// 95768 North Head    — clifftop at the harbour mouth, ~1.5 km from the swim patch.
//                       THE station for this app. Wind only (no air temp there).
// 95766 Sydney Harbour— Wedding Cake West beacon, mid-harbour, over-water.
// 94769 Fort Denison  — inner harbour, sheltered; kept only as a sanity cross-check.
var OBS_STATIONS = {
  northhead:     95768,
  sydneyharbour: 95766,
  fortdenison:   94769,
};

// BOM publishes on the half hour and the file is regenerated a few minutes later.
// 240s keeps every swimmer's app off BOM's origin without ever serving a reading
// that is more than one cycle stale.
var OBS_CACHE_TTL = 240;

// Ceiling on ?hours=. BOM ships 72 h; the today-graph only ever shows the current
// day, and an hourly aggregate of 36 h is ~2 KB — small enough to be uninteresting
// next to the forecast payload, which is the test this trim has to pass.
var OBS_HIST_MAX_H = 36;

async function handleObs(request, env, url, ctx) {
  if (request.method !== "GET") return errJSON("Method not allowed", 405);

  const key = String(url.searchParams.get("station") || "northhead").toLowerCase();
  const wmo = OBS_STATIONS[key];
  if (!wmo) return errJSON("Unknown station", 400);

  // ?hours=N — ask for the observed HISTORY as well as the nowcast rows. Used to
  // redraw the past of the today-graph with what the wind actually did, instead of
  // what was forecast for it. Absent → byte-identical to the original response, so
  // an old client and a new one can hit the same route.
  var histH = parseInt(url.searchParams.get("hours") || "0", 10);
  if (!isFinite(histH) || histH < 0) histH = 0;
  if (histH > OBS_HIST_MAX_H) histH = OBS_HIST_MAX_H;

  // ONE KV entry regardless of the parameter, holding the full aggregate: the
  // per-request slice happens below. Keyed :v2 so a body cached by the previous
  // version of this route (no `hourly` key) is not served to a client asking for
  // history — it would look like a station with no past, which is indistinguishable
  // from a broken feed.
  const kvKey = "obs:" + key + ":v2";

  // The per-request shape. The cached body always carries the full history; a client
  // that did not ask for it gets no `hourly` key at all rather than an empty array,
  // so "this route has no history" and "this station has no history" stay
  // distinguishable at the client.
  function shape(full) {
    if (!histH) { var o = Object.assign({}, full); delete o.hourly; return o; }
    var all = Array.isArray(full.hourly) ? full.hourly : [];
    return Object.assign({}, full, { hourly: all.slice(Math.max(0, all.length - histH)) });
  }

  // Serve from KV first. Note this is deliberately NOT a stale-if-error cache with
  // an unbounded lifetime: a wind observation that is hours old is worse than no
  // observation, because the client would silently bias-correct against it. The
  // client re-checks age itself, but we refuse to hand out anything truly ancient.
  if (env.RATE) {
    try {
      const hit = await env.RATE.get(kvKey, { cacheTtl: 60 });
      if (hit) {
        const parsed = JSON.parse(hit);
        if (parsed && parsed.cachedAt && (Date.now() - parsed.cachedAt) < OBS_CACHE_TTL * 1000) {
          return new Response(JSON.stringify(shape(parsed)), {
            status: 200,
            // Vary on nothing at the CDN: the URL carries `hours`, so the two shapes
            // are already different cache keys.
            headers: JSONH({ "Cache-Control": "public, max-age=120, s-maxage=120" })
          });
        }
      }
    } catch (_) { /* KV miss/parse error → fall through to a live fetch */ }
  }

  // BOM refuses the default Workers UA, AND it 403s any UA carrying a crawler
  // signature. Measured 13 Aug 2026, same URL, same minute:
  //   "Mozilla/5.0 (compatible; SwimManly/1.0; +https://app.viz.net.au)" → 403
  //   "SwimManly/1.0 (+https://app.viz.net.au)"                          → 403
  //   "SwimManly/1.0 (contact: sticasale@gmail.com)"                     → 403
  //   "SwimManly/1.0 (app.viz.net.au)"                                   → 200
  //   "Mozilla/5.0"                                                      → 200
  // It is the "+scheme://" / "contact:" pattern that trips it, not the app name
  // and not the Cloudflare egress IP — the identical string succeeds and fails
  // from the same host. So the polite convention of advertising a contact URL is
  // precisely what gets blocked. UA_PRIMARY still names the app and its domain
  // honestly; UA_FALLBACK is the generic string, tried ONCE if BOM ever tightens
  // further, so a UA policy change degrades to a retry instead of an outage.
  const UA_PRIMARY  = "SwimManly/1.0 (app.viz.net.au)";
  const UA_FALLBACK = "Mozilla/5.0";

  const bomURL = "https://www.bom.gov.au/fwo/IDN60801/IDN60801." + wmo + ".json";
  async function hitBOM(ua) {
    return fetch(bomURL, {
      headers: { "User-Agent": ua, "Accept": "application/json" },
      // cacheKey pinned per-UA: without it the two attempts share a cache entry
      // and a cached 403 would be replayed to the fallback, defeating the retry.
      cf: { cacheTtl: 120, cacheEverything: true, cacheKey: bomURL + "|" + ua },
    });
  }

  let upstream;
  try {
    upstream = await hitBOM(UA_PRIMARY);
    if (upstream.status === 403) upstream = await hitBOM(UA_FALLBACK);
  } catch (e) {
    return errJSON("BOM unreachable: " + (e && e.message || "fetch failed"), 502);
  }
  if (!upstream.ok) return errJSON("BOM " + upstream.status, 502);

  let raw;
  try { raw = await upstream.json(); } catch (_) { return errJSON("BOM bad JSON", 502); }

  const rows = (raw && raw.observations && raw.observations.data) || [];
  if (!rows.length) return errJSON("BOM empty", 502);

  // Trim hard. BOM ships 72 h (~174 rows, ~90 KB); the client only ever needs the
  // last ~2 h to vector-average, and shipping the rest to every phone on mobile
  // data would be the single largest payload in the app for no benefit.
  const slim = rows.slice(0, 8).map(function (r) {
    return {
      t:    r.local_date_time_full || null,  // yyyymmddHHMMSS, station local time
      utc:  r.aifstime_utc || null,          // yyyymmddHHMMSS UTC — the one to parse
      kmh:  (typeof r.wind_spd_kmh === "number") ? r.wind_spd_kmh : null,
      gust: (typeof r.gust_kmh === "number") ? r.gust_kmh : null,
      dir:  (typeof r.wind_dir === "string" && r.wind_dir !== "-") ? r.wind_dir : null,
    };
  });

  // ── Hourly aggregate of the observed history ────────────────────────────────
  // BOM reports a 10-minute mean every 30 minutes; the app's matrix is hourly. So
  // the readings are folded into clock hours HERE, not on the phone: it is the same
  // arithmetic for every client and it is what makes the payload small.
  //
  // Grouped on the UTC hour, which is safe because Sydney's offset is a whole number
  // of hours — the UTC hour boundary and the local one are the same instant. Speed is
  // a scalar mean and direction a vector mean, the same split parseWindObs uses and
  // for the same reason: under a veering wind the vector magnitude collapses toward
  // zero and would understate the wind that actually blew.
  var DIR_DEG = {
    N:0, NNE:22.5, NE:45, ENE:67.5, E:90, ESE:112.5, SE:135, SSE:157.5,
    S:180, SSW:202.5, SW:225, WSW:247.5, W:270, WNW:292.5, NW:315, NNW:337.5,
  };
  function buildHourly(rows, maxHours) {
    var cutoff = Date.now() - maxHours * 3600 * 1000;
    var acc = {};
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var s = r.aifstime_utc;
      if (!s || s.length < 14) continue;
      var t = Date.UTC(+s.slice(0,4), +s.slice(4,6) - 1, +s.slice(6,8),
                       +s.slice(8,10), +s.slice(10,12), +s.slice(12,14));
      if (!isFinite(t) || t < cutoff) continue;
      // Same refusal as the client: BOM applies no QC, so a null or absurd value is
      // dropped rather than averaged in.
      if (typeof r.wind_spd_kmh !== "number" || !isFinite(r.wind_spd_kmh) ||
          r.wind_spd_kmh < 0 || r.wind_spd_kmh > 200) continue;
      var deg = (typeof r.wind_dir === "string") ? DIR_DEG[r.wind_dir] : undefined;
      if (deg === undefined) continue;
      var hk = s.slice(0, 10);                       // yyyymmddHH
      var a = acc[hk] || (acc[hk] = { n:0, spd:0, u:0, v:0, gust:null });
      var rad = deg * Math.PI / 180;
      a.n++; a.spd += r.wind_spd_kmh;
      a.u += r.wind_spd_kmh * Math.sin(rad);
      a.v += r.wind_spd_kmh * Math.cos(rad);
      if (typeof r.gust_kmh === "number" && isFinite(r.gust_kmh)) {
        a.gust = (a.gust == null) ? r.gust_kmh : Math.max(a.gust, r.gust_kmh);
      }
    }
    var out = [];
    Object.keys(acc).sort().forEach(function (hk) {
      var a = acc[hk];
      var dir = Math.atan2(a.u / a.n, a.v / a.n) * 180 / Math.PI;
      if (dir < 0) dir += 360;
      out.push({
        utcHour: hk,                                  // yyyymmddHH, hour START
        kmh:  Math.round((a.spd / a.n) * 10) / 10,
        gust: a.gust,
        dir:  Math.round(dir * 10) / 10,
        n:    a.n,
      });
    });
    return out;
  }

  const body = {
    station:  key,
    wmo:      wmo,
    name:     rows[0].name || null,
    lat:      rows[0].lat != null ? rows[0].lat : null,
    lon:      rows[0].lon != null ? rows[0].lon : null,
    // BOM's own disclaimer applies and is worth carrying: these values are NOT
    // quality-controlled, so nulls and spurious readings do occur.
    qc:       false,
    obs:      slim,
    // Always BUILT at full depth and cached at full depth; sliced per request below.
    // Building it costs one pass over rows we already parsed, and it means a client
    // asking for 6 h and one asking for 24 h share a single BOM fetch and a single
    // KV entry.
    hourly:   buildHourly(rows, OBS_HIST_MAX_H),
    cachedAt: Date.now(),
  };

  if (env.RATE) {
    // expirationTtl floor is 60s in KV; 900 keeps a last-known-good around a
    // little past its useful life purely so a BOM blip doesn't cause a stampede.
    const put = env.RATE.put(kvKey, JSON.stringify(body), { expirationTtl: 900 }).catch(function () {});
    // waitUntil if we were given a ctx, otherwise just let it float — a failed
    // cache write must never fail the response.
    try { if (ctx && ctx.waitUntil) ctx.waitUntil(put); } catch (_) {}
  }

  return new Response(JSON.stringify(shape(body)), {
    status: 200,
    headers: JSONH({ "Cache-Control": "public, max-age=120, s-maxage=120" })
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// ── ROUTING (ALREADY DEPLOYED — recorded here, nothing to do) ────────────────
// Inside the fetch handler, FIND this line (added when /tune was deployed):
//
//     if (url.pathname === "/tune") return handleTune(request, env, url, ctx);
//
// REPLACE it with those two lines:
//
//     if (url.pathname === "/tune") return handleTune(request, env, url, ctx);
//     if (url.pathname === "/obs") return handleObs(request, env, url, ctx);
//
// Anchoring on the /tune line rather than on the 404 fallback is deliberate: it
// is known-good, and it guarantees the new route lands ABOVE the fallback. If
// /obs is pasted BELOW the "Not found" return it will 404 forever.
//
// No auth: this is public BOM data, read by every swimmer's app at boot, exactly
// like the GET side of /tune. OPTIONS is already handled at the top of fetch, so
// CORS preflight needs nothing here.
//
// ── VERIFY AFTER SAVING ─────────────────────────────────────────────────────
// 1. THE REGRESSION THAT MATTERS. The deployed app calls /obs with no `hours` on
//    every boot, and the Forecast App shares this Worker, so check the Worker is
//    alive and the old shape is unchanged BEFORE anything else:
//      curl -s ".../obs?station=northhead" | grep -c hourly           -> 0
//      curl -s ".../forecast?days=5" | head -c 80                     -> JSON, not an error
//    A 500 on either means the paste landed badly — revert to the previous
//    version in the dashboard's deployment list, do not debug it live.
// 2. The new shape:
//      curl -s ".../obs?station=northhead&hours=24" | grep -c hourly  -> 1
// 3. Run 2 twice. The second is a KV hit, and `hourly` must be there both times —
//    that is the :v2 key doing its job.
// 4. Cron Triggers still list BOTH. This Worker has lost them to a deploy before.
