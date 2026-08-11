// ─────────────────────────────────────────────────────────────────────────────
// /tune route for the bold-rain-6ded Worker — paste into the DASHBOARD editor.
//
// WHY THE DASHBOARD, NOT WRANGLER: this Worker is dashboard-managed. Deploying it
// from a local copy has previously stripped the KV binding and the two cron
// triggers silently. Editing in the dashboard touches only the script and leaves
// bindings and schedules alone.
//
// WHAT IT DOES
//   GET  /tune  → { values:{...}, savedAt, by }  (public, cacheable, no auth)
//   POST /tune  → stores { values } in KV        (requires X-Admin-Token)
// The app reads GET at boot and applies the values under each device's own
// slider overrides. Only the six LOCAL scoring knobs are accepted; anything else
// in the payload is dropped, so a stray or malicious key can never reach SITE.
//
// ── HOW TO INSTALL ──────────────────────────────────────────────────────────
// 1. Dashboard → Workers → bold-rain-6ded → Edit code.
// 2. Paste the ALLOWED/handleTune block below near the top of the module.
// 3. Inside the existing fetch handler, before the final 404, add:
//        if (url.pathname === '/tune') return handleTune(request, env, url);
// 4. Check the KV binding name at Settings → Variables → KV Namespace Bindings
//    and set KV_BINDING below to match (the code guesses common names but do not
//    rely on that — a wrong name means every push silently 500s).
// 5. Confirm the admin token variable name matches the one /board-status already
//    uses (ADMIN_TOKEN below) — reusing it is the point, no new secret.
// 6. Save & Deploy. Then in the app: ?tune=1 → move a LOCAL slider → Push.
//
// VERIFY:  curl https://bold-rain-6ded.sticasale.workers.dev/tune
//          → {"values":{},...} once deployed;  404 means step 3 was missed.
// ─────────────────────────────────────────────────────────────────────────────

// Whitelist — MUST match TUNE_LIVE_KEYS in index.html. Each entry carries the
// range the app's own slider allows, and values outside it are rejected: the
// Worker is the last line of defence between a typo and everyone's forecast.
const TUNE_ALLOWED = {
  offshorePts:    [0, 25],
  offshoreArc:    [0, 90],
  offshoreMaxPts: [0, 40],
  chopLeeFactor:  [0.3, 1],
  chopLeeFrom:    [90, 270],
  chopLeeTo:      [180, 359],
};

const KV_KEY = 'tune-overrides-v1';

async function handleTune(request, env, url) {
  // Match your actual binding name here (Settings → Variables → KV bindings).
  const KV = env.SWIM_KV || env.KV || env.CACHE;
  const CORS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type, X-Admin-Token',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  };
  const json = (obj, status) => new Response(JSON.stringify(obj), {
    status: status || 200,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', ...CORS },
  });

  if (request.method === 'OPTIONS') return new Response(null, { headers: CORS });
  if (!KV) return json({ error: 'kv-binding-missing' }, 500);

  if (request.method === 'GET') {
    const raw = await KV.get(KV_KEY);
    return json(raw ? JSON.parse(raw) : { values: {}, savedAt: null });
  }

  if (request.method === 'POST') {
    const token = request.headers.get('X-Admin-Token') || '';
    // Same secret /board-status checks — rename if yours differs.
    if (!env.ADMIN_TOKEN || token !== env.ADMIN_TOKEN) return json({ error: 'unauthorised' }, 401);

    let body;
    try { body = await request.json(); } catch (e) { return json({ error: 'bad-json' }, 400); }
    const incoming = (body && body.values) || {};

    const values = {};
    const rejected = [];
    for (const k of Object.keys(TUNE_ALLOWED)) {
      const v = Number(incoming[k]);
      if (incoming[k] == null) continue;
      const [lo, hi] = TUNE_ALLOWED[k];
      if (!isFinite(v) || v < lo || v > hi) { rejected.push(k); continue; }
      values[k] = v;
    }
    if (!Object.keys(values).length) return json({ error: 'no-valid-values', rejected }, 400);

    const record = { values, savedAt: new Date().toISOString(), by: 'tune-panel' };
    await KV.put(KV_KEY, JSON.stringify(record));
    return json({ ok: true, ...record, rejected });
  }

  return json({ error: 'method-not-allowed' }, 405);
}
