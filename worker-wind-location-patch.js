// ─────────────────────────────────────────────────────────────────────────────
// LOCAL-LOCATION OVERLAY — bold-rain-6ded Worker
//
// WHAT CHANGES: WIND and TIDES come from Cabbage Tree Bay (23217) — the swim
// location itself. SWELL keeps coming from Long Reef Point (23127), which is what
// that node was chosen for: an unsheltered offshore point for a clean oceanside
// swell read. Nothing else moves.
//
// WHY: 23127 was picked when WillyWeather supplied the SCORED swell. Swell now
// comes from the NSW Nearshore feed (node 103218) and WW swell survives only as
// the `_ww` "Oceanside" FYI — so the only WW inputs that still matter are wind and
// tide, and 23127 is ~8 km NNE of the bay.
//
// MEASURED 13 Aug 2026 — 72 h of WW wind, four locations:
//   Cabbage Tree Bay (23217) vs Manly (2234)       : IDENTICAL 72/72
//   Cabbage Tree Bay (23217) vs North Head (19145) : IDENTICAL 72/72
//   Cabbage Tree Bay (23217) vs Long Reef Pt(23127): mean -4.15 km/h, max 13.3,
//                                                    direction to 45 deg, 0/72 same
// The BOM grid is coarse enough that the whole bay/headland area is ONE cell —
// which is the user's own observation, confirmed — but Long Reef Point is a
// DIFFERENT cell running ~5 km/h windier. Moving wind to 23217 puts the forecast
// in the same cell as the North Head observations we log against.
//   Tides 23217 vs 23127: heights identical, turning points within 1 minute.
//   Swell: 23217 returns NULL for swell, so 23127 must stay for that.
//
// *** THE WORKER IS SHARED — READ THIS BEFORE PASTING ***
// bold-rain-6ded also serves the FORECAST APP (separate project, hundreds of live
// users), which calls the SAME /forecast route with `?days=5` and no `location`
// param. Its scoring is calibrated on Long Reef wind. So the overlay below is
// GATED on `location=swimmers`, which the Manly Swim app already sends today and
// the Worker currently ignores:
//     Manly Swim   -> ?location=swimmers&days=7&t=...   -> overlay applied
//     Forecast App -> ?days=5                           -> untouched, byte for byte
// The gate also skips the second WW call entirely for the Forecast App, so it
// costs that app nothing in latency or WW quota. Do NOT make the overlay
// unconditional. (forecast_history in PATCH 4 is Manly-only: the Forecast App has
// zero references to that table, verified 13 Aug 2026.)
//
// WHY AN OVERLAY AND NOT JUST CHANGING WW_LOCATION:
//   0. IT WOULD BREAK THE OTHER APP. See above — this alone is decisive.
//   1. SWELL. 23217 has none. A straight swap kills the Oceanside block and the
//      WW-vs-NS compare tick.
//   2. WW_LOCATION IS USED TWICE. Besides /forecast, fetchPrimaryFromWW() feeds
//      logHour() -> forecast_history.wind_kmh/wind_dir on the cron. Changing only
//      the route would keep logging Long Reef wind into the very history table the
//      observed-vs-forecast calibration is measured against.
//   3. FALLBACK IS FREE. The main 23127 call already returns wind AND tides, so if
//      the 23217 call fails we simply do not overlay and behaviour is exactly what
//      it is today. No hole, no special case.
//
// PASTE IN THE DASHBOARD EDITOR, NOT WRANGLER: this Worker is dashboard-managed;
// deploying from a local copy has stripped its KV binding and cron triggers before.
// ─────────────────────────────────────────────────────────────────────────────


// ── PATCH 1 of 4 — add the local-location constant ──────────────────────────
// FIND:
//     var WW_LOCATION = 23127;
// REPLACE WITH:

var WW_LOCATION = 23127;
// Cabbage Tree Bay — the swim location. Serves wind + tides, but NOT swell, which
// is why 23127 stays above. Same BOM forecast cell as North Head, where the
// observations we calibrate against are measured. Do NOT merge these constants.
var WW_LOCAL_LOCATION = 23217;


// ── PATCH 2 of 4 — add the local wind+tide fetch helper ─────────────────────
// FIND:
//     async function fetchPrimaryFromWW() {
// PASTE THIS ENTIRE BLOCK DIRECTLY ABOVE THAT LINE (leave that line in place):

async function fetchWWLocal(days) {
  try {
    const r = await fetch(
      `${WW_BASE}/${WW_KEY}/locations/${WW_LOCAL_LOCATION}/weather.json?forecasts=wind,tides&days=${days}`,
      { cf: { cacheTtl: FORECAST_EDGE_TTL_SEC, cacheEverything: true } }
    );
    if (!r.ok) return null;
    const j = await r.json();
    const f = (j && j.forecasts) || {};
    // Count entries rather than trusting the shape. WW returns a PRESENT-but-null
    // block for data a location does not carry (measured: location 2234 returns
    // tides: null, 23217 returns swell: null), and an empty block that overwrote a
    // good one would silently flatten the tide term with nothing looking broken.
    const count = (b) => {
      if (!b || !Array.isArray(b.days)) return 0;
      let n = 0;
      for (const d of b.days) n += ((d && d.entries) || []).length;
      return n;
    };
    return {
      wind:  count(f.wind)  ? f.wind  : null,
      tides: count(f.tides) ? f.tides : null,
    };
  } catch (_) {
    return null;   // caller keeps the 23127 values — i.e. today's behaviour
  }
}
__name(fetchWWLocal, "fetchWWLocal");
__name2(fetchWWLocal, "fetchWWLocal");


// ── PATCH 3 of 4 — /forecast route: overlay local wind + tides ──────────────
// FIND (the whole remainder of the fetch handler, after the
// `if (url.pathname !== "/forecast")` 404 guard) — from this line:
//
//     const days = String(Math.min(Math.max(parseInt(url.searchParams.get("days"), 10) || 5, 1), 7));
//
// ...down to and including the closing brace of its catch block:
//
//     } catch (err) {
//       return new Response(
//         JSON.stringify({ error: err.message }),
//         { status: 500, headers: JSONH() }
//       );
//     }
//
// REPLACE ALL OF THAT WITH:

    const days = String(Math.min(Math.max(parseInt(url.searchParams.get("days"), 10) || 5, 1), 7));
    // THE GATE. Only the Manly Swim app sends location=swimmers. The Forecast App
    // sends ?days=5 with no location and must come out of here byte-identical to
    // today — same body, no _sources key, and no second WW call on its behalf.
    const wantLocal = url.searchParams.get("location") === "swimmers";
    const wwUrl = `${WW_BASE}/${WW_KEY}/locations/${WW_LOCATION}/weather.json?forecasts=swell,tides,wind&days=${days}`;
    try {
      // In parallel — the local call must not add latency on top of the main one.
      // fetchWWLocal never throws, so Promise.all is safe.
      const [wwRes, local] = await Promise.all([
        fetch(wwUrl, { cf: { cacheTtl: FORECAST_EDGE_TTL_SEC, cacheEverything: true } }),
        wantLocal ? fetchWWLocal(days) : Promise.resolve(null)
      ]);
      if (!wwRes.ok) {
        const txt = await wwRes.text();
        return new Response(
          JSON.stringify({ error: `WillyWeather ${wwRes.status}`, detail: txt }),
          { status: wwRes.status, headers: JSONH() }
        );
      }
      const data = await wwRes.json();
      // Overlay ONLY what Cabbage Tree Bay actually returned. Swell is never
      // touched — 23217 has none, and the offshore node is the right source for
      // the Oceanside FYI anyway. Each block falls back independently.
      if (wantLocal) {
        const from = { wind: WW_LOCATION, tides: WW_LOCATION, swell: WW_LOCATION };
        if (local && data && data.forecasts) {
          if (local.wind)  { data.forecasts.wind  = local.wind;  from.wind  = WW_LOCAL_LOCATION; }
          if (local.tides) { data.forecasts.tides = local.tides; from.tides = WW_LOCAL_LOCATION; }
        }
        data._sources = from;   // provenance: a curl can tell exactly what it got
      }
      return new Response(JSON.stringify(data), {
        status: 200,
        headers: JSONH({ "Cache-Control": `public, max-age=${FORECAST_EDGE_TTL_SEC}, s-maxage=${FORECAST_EDGE_TTL_SEC}` })
      });
    } catch (err) {
      return new Response(
        JSON.stringify({ error: err.message }),
        { status: 500, headers: JSONH() }
      );
    }


// ── PATCH 4 of 4 — forecast_history logger must log the SAME wind + tide ────
// Without this the cron keeps writing Long Reef values into forecast_history while
// the app shows Cabbage Tree Bay ones — and forecast_history is the baseline the
// observed-vs-forecast calibration is computed from. They must not diverge.
//
// FIND (inside fetchPrimaryFromWW — note the tide block that follows it):
//
//     const wind = nearestEntry(j?.forecasts?.wind?.days, nowMs);
//     if (wind) {
//       out.wind_kmh = wind.speed ?? null;
//       out.wind_dir = wind.direction ?? null;
//     }
//     const tideEntries = (j?.forecasts?.tides?.days || []).flatMap((d) => d.entries || []).filter((e) => e && e.dateTime).sort((a, b) => localMs(a.dateTime) - localMs(b.dateTime));
//
// REPLACE THOSE (keep everything after the tideEntries line unchanged) WITH:

    // Wind + tide from Cabbage Tree Bay, matching what /forecast serves. Falls
    // back per-block to this response's own 23127 values, so a logged hour is
    // never blank — at worst it is from the old location.
    const local = await fetchWWLocal(2);
    const wind = nearestEntry(
      (local && local.wind && local.wind.days) || j?.forecasts?.wind?.days,
      nowMs
    );
    if (wind) {
      out.wind_kmh = wind.speed ?? null;
      out.wind_dir = wind.direction ?? null;
    }
    const tideEntries = (((local && local.tides && local.tides.days) || j?.forecasts?.tides?.days) || []).flatMap((d) => d.entries || []).filter((e) => e && e.dateTime).sort((a, b) => localMs(a.dateTime) - localMs(b.dateTime));


// ── VERIFY AFTER DEPLOY ─────────────────────────────────────────────────────
//   MANLY SWIM path (overlay expected):
//   curl.exe -s "https://bold-rain-6ded.sticasale.workers.dev/forecast?location=swimmers&days=1"
//     "_sources":{"wind":23217,"tides":23217,"swell":23127}   <- the good case
//                 wind/tides 23127 means the CTB call failed and it fell back
//     forecasts.tides.days[0].entries  MUST be non-empty (~4)  <- the trap
//     forecasts.swell.days[0].entries  MUST be non-empty (~24) <- untouched
//
//   FORECAST APP path (MUST be unchanged — this is the regression test that
//   matters, because that app has hundreds of live users):
//   curl.exe -s "https://bold-rain-6ded.sticasale.workers.dev/forecast?days=5"
//     NO "_sources" key at all, and the wind must still be Long Reef's. Compare a
//     couple of speeds against a pre-deploy capture; they must match exactly.
//   The app needs NO change; index.html ignores the extra _sources key.
//   Cron triggers must still list BOTH after the paste (Settings -> Triggers).
//
// ── WHAT TO WATCH AFTER ─────────────────────────────────────────────────────
//   Scores WILL shift, and should. Every wind-driven knob (chopLeeFactor,
//   bl_chop_impact, the CHOP cm thresholds, the offshore-westerly credit) was
//   tuned on Long Reef wind running ~5 km/h stronger, so expect calmer CHOP calls
//   on day one. That is the input getting more correct, not the model getting worse
//   — but it does mean the knobs are now fitted to a wind that no longer arrives.
//   The test that this was right: median(obs/forecast) in wind_obs_pairs was 0.73
//   against Long Reef. Against Cabbage Tree Bay it should move to roughly 0.85-0.90.
//   If it does not move, the location was not the problem and only the
//   sector-dependent k correction is left to do.
// ─────────────────────────────────────────────────────────────────────────────
