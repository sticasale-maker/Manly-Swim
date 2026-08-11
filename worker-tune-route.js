// ─────────────────────────────────────────────────────────────────────────────
// /tune route for the bold-rain-6ded Worker.
//
// Written against the ACTUAL deployed source (worker.patched.js), so the names
// below are the real ones, not guesses:
//   • KV binding      → env.RATE          (same namespace as ns:session, rate caps)
//   • auth            → isAuthed(request, url, env) → env.ADMIN_PASSPHRASE
//                       It already accepts the X-Admin-Token header the tune panel
//                       sends, so this is the same passphrase as /board-status.
//   • helpers         → CORS, JSONH(), errJSON() already exist in the file.
//
// PASTE IN THE DASHBOARD EDITOR, NOT WRANGLER: this Worker is dashboard-managed;
// deploying from a local copy has stripped its KV binding and cron triggers before.
//
// ── BLOCK 1 of 2 ────────────────────────────────────────────────────────────
// Paste this anywhere at top level — e.g. directly ABOVE the line
//     var worker_patched_default = {
// ─────────────────────────────────────────────────────────────────────────────

// Whitelist. Keys MUST match TUNE_LIVE_KEYS in index.html, and each range MUST
// match that knob's slider min/max. This is the last line of defence between a
// fat-fingered value and every swimmer's forecast, so it revalidates server-side
// rather than trusting the panel that sent it. Anything not listed is dropped.
var TUNE_ALLOWED = {
  offshorePts:    [0, 25],    // Entry points credited at 30 km/h due W
  offshoreArc:    [0, 90],    // half-width of the flattening sector
  offshoreMaxPts: [0, 40],    // ceiling on that credit
  chopLeeFactor:  [0.3, 1],   // wind multiplier through the sheltered land arc
  chopLeeFrom:    [90, 270],  // arc start
  chopLeeTo:      [180, 359], // arc end
};
var TUNE_KV_KEY = "tune-overrides-v1";

async function handleTune(request, env, url, ctx) {
  if (!env.RATE) return errJSON("KV binding RATE missing", 500);

  // GET is public and unauthenticated ON PURPOSE — every swimmer's app reads it
  // at boot. It exposes six scoring constants, nothing personal. Short cache so
  // a push propagates within the hour without hammering KV.
  if (request.method === "GET") {
    let raw = null;
    try { raw = await env.RATE.get(TUNE_KV_KEY, { cacheTtl: 300 }); } catch (_) {}
    const body = raw ? raw : JSON.stringify({ values: {}, savedAt: null });
    return new Response(body, {
      status: 200,
      headers: JSONH({ "Cache-Control": "public, max-age=300, s-maxage=300" })
    });
  }

  if (request.method === "POST") {
    if (!isAuthed(request, url, env)) return errJSON("Unauthorised", 401);

    let payload;
    try { payload = JSON.parse(await request.text()); } catch (_) { return errJSON("Bad JSON", 400); }
    const incoming = payload && payload.values || {};

    const values = {};
    const rejected = [];
    for (const k of Object.keys(TUNE_ALLOWED)) {
      if (incoming[k] == null) continue;
      const v = Number(incoming[k]);
      const lo = TUNE_ALLOWED[k][0], hi = TUNE_ALLOWED[k][1];
      if (!Number.isFinite(v) || v < lo || v > hi) { rejected.push(k); continue; }
      values[k] = v;
    }
    if (!Object.keys(values).length) return errJSON("No valid values (rejected: " + rejected.join(",") + ")", 400);

    // Merge onto what is already stored, so a partial push cannot silently reset
    // the knobs it did not mention back to their coded defaults.
    let prev = {};
    try {
      const raw = await env.RATE.get(TUNE_KV_KEY);
      if (raw) prev = JSON.parse(raw).values || {};
    } catch (_) {}

    const record = {
      values: { ...prev, ...values },
      savedAt: new Date().toISOString(),
      by: "tune-panel"
    };
    // No expirationTtl — these are settings, not a cache. A TTL would silently
    // revert everyone to coded defaults weeks later with no one touching a thing.
    try { await env.RATE.put(TUNE_KV_KEY, JSON.stringify(record)); }
    catch (err) { return errJSON("KV write failed: " + (err && err.message || err), 502); }

    return new Response(JSON.stringify({ ok: true, ...record, rejected }), {
      status: 200,
      headers: JSONH({ "Cache-Control": "no-store" })
    });
  }

  return errJSON("Method not allowed", 405);
}

// ─────────────────────────────────────────────────────────────────────────────
// ── BLOCK 2 of 2 ────────────────────────────────────────────────────────────
// Inside the fetch handler, paste the ONE line below immediately ABOVE:
//
//     if (url.pathname !== "/forecast") {
//       return new Response("Not found", { status: 404, headers: CORS });
//     }
//
// (It must come before that 404 fallback, or /tune will 404 forever. OPTIONS is
// already handled at the very top of fetch, so CORS preflight needs nothing here.)
//
//     if (url.pathname === "/tune") return handleTune(request, env, url, ctx);
//
// ── VERIFY AFTER DEPLOY ─────────────────────────────────────────────────────
//   curl https://bold-rain-6ded.sticasale.workers.dev/tune
//     → {"values":{},"savedAt":null}   route is live
//     → Not found                       Block 2 line is missing or below the 404
//   Then: app ?tune=1 → move a LOCAL slider → "Push LOCAL knobs to live"
//         → re-run the curl and the values should be there.
// ─────────────────────────────────────────────────────────────────────────────
