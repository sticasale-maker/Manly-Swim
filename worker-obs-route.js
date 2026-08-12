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
// ── BLOCK 1 of 2 ────────────────────────────────────────────────────────────
// Paste this anywhere at top level — e.g. directly ABOVE the line
//     var worker_patched_default = {
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

async function handleObs(request, env, url, ctx) {
  if (request.method !== "GET") return errJSON("Method not allowed", 405);

  const key = String(url.searchParams.get("station") || "northhead").toLowerCase();
  const wmo = OBS_STATIONS[key];
  if (!wmo) return errJSON("Unknown station", 400);

  const kvKey = "obs:" + key;

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
          return new Response(JSON.stringify(parsed), {
            status: 200,
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

  return new Response(JSON.stringify(body), {
    status: 200,
    headers: JSONH({ "Cache-Control": "public, max-age=120, s-maxage=120" })
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// ── BLOCK 2 of 2 ────────────────────────────────────────────────────────────
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
// ── VERIFY AFTER DEPLOY ─────────────────────────────────────────────────────
//   curl "https://bold-rain-6ded.sticasale.workers.dev/obs?station=northhead"
//     → {"station":"northhead","wmo":95768,"name":"North Head",...}   live
//     → Not found        Block 2 is missing, or landed below the 404 fallback
//     → "Unknown station"  Block 1 pasted but OBS_STATIONS didn't come with it
//   Then confirm the crons SURVIVED (a stripped cron is the failure mode this
//   Worker has actually had before — see the deploy rule):
//     Dashboard → Settings → Triggers → Cron Triggers must still list BOTH.
// ─────────────────────────────────────────────────────────────────────────────
