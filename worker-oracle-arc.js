// ─────────────────────────────────────────────────────────────────────────────
// PASTE JOB — bold-rain-6ded, Cloudflare dashboard editor. NOT deployed from this
// repo (CLAUDE.md §6). SUPERSEDES worker-summary-chop-and-noswim.js, now deleted:
// its two edits are folded in here as edits 4 and 5.
//
// FIVE edits to /swim-summary plus ONE line of SQL. No route defaults change, no
// bindings, no crons.
//
// WHY ALL AT ONCE: the client already computes and sends the water temperature,
// the arrival hour and the departure hour. This route builds `facts` from an
// explicit field list, so it drops all three. Until this is pasted, the app looks
// exactly as it did.
//
//   SQL      conditions_summary needs one column
//   EDIT 1   cache read — return the new third paragraph
//   EDIT 2   facts — accept seaTemp / arrival / exit
//   EDIT 3   SWIMSUM_SYS — the swimmer's arc (see oracle-prompt.txt)
//   EDIT 4   summaryConflict — the no-swim guard stops being conditional
//   EDIT 5   response + cache write — carry after_text
//
// ─────────────────────────────────────────────────────────────────────────────
// SQL — once, in the Supabase SQL editor. Safe before or after the paste: old
// cached rows return null and render without the closing beat.
//
//     alter table public.conditions_summary
//       add column if not exists after_text text;
//
// ─────────────────────────────────────────────────────────────────────────────
// EDIT 1 — the cache read. Two small changes on one statement: select the column,
// and pass it back.
//
// FIND:
//     `${SUPABASE_URL}/rest/v1/conditions_summary?bucket=eq.${encodeURIComponent(bucket)}&select=wx_text,water_text`,
// REPLACE:
//     `${SUPABASE_URL}/rest/v1/conditions_summary?bucket=eq.${encodeURIComponent(bucket)}&select=wx_text,water_text,after_text`,
//
// FIND:
//     JSON.stringify({ wx_text: rows[0].wx_text, water_text: rows[0].water_text, cached: true }),
// REPLACE:
//     JSON.stringify({ wx_text: rows[0].wx_text, water_text: rows[0].water_text, after_text: rows[0].after_text || "", cached: true }),
//
// ─────────────────────────────────────────────────────────────────────────────
// EDIT 2a — add this helper at top level, next to numClamp().

function shapeMoment(m) {
  if (!m || typeof m !== "object") return null;
  const airC = numClamp(m.airC, -10, 55, null);
  const windKmh = numClamp(m.windKmh, 0, 150, null);
  if (airC == null && windKmh == null) return null;
  return {
    hhmm: String(m.hhmm || "").slice(0, 5),
    airC,
    windKmh,
    windDir: String(m.windDir || "").slice(0, 4),
    rainProb: numClamp(m.rainProb, 0, 100, null)
  };
}

// EDIT 2b — the facts whitelist. FIND the end of the water block:
//
//           swellTrend: ["building", "easing", "holding"].includes(wa.swellTrend) ? wa.swellTrend : "holding",
//           shelteredContrast: !!wa.shelteredContrast
//         }
//       };
//
// REPLACE WITH:
//
//           swellTrend: ["building", "easing", "holding"].includes(wa.swellTrend) ? wa.swellTrend : "holding",
//           shelteredContrast: !!wa.shelteredContrast
//         },
//         // WATER TEMPERATURE. Absent unless the client sent a FRESH reading — it
//         // withholds anything older than two days, because one Facebook post is
//         // the only source. ageDays travels with it so the prose can date it out
//         // loud. null means the model is told nothing, never something wrong.
//         seaTemp: (f.seaTemp && numClamp(f.seaTemp.c, 5, 32, null) != null)
//           ? { c: numClamp(f.seaTemp.c, 5, 32, null), ageDays: numClamp(f.seaTemp.ageDays, 0, 30, 0) }
//           : null,
//         // ARRIVING and GETTING OUT — the hours either side of the swim. Clamped
//         // like every other number here: this route trusts the client's
//         // measurements, never its arithmetic.
//         arrival: shapeMoment(f.arrival),
//         exit:    shapeMoment(f.exit)
//       };
//
// ─────────────────────────────────────────────────────────────────────────────
// EDIT 3 — SWIMSUM_SYS. Replace the whole template literal with the contents of
// oracle-prompt.txt, kept beside this file so it stays readable and diffable.
//
// ─────────────────────────────────────────────────────────────────────────────
// EDIT 4 — summaryConflict, inside the dangerous-day block. FIND:
//
//     if (facts.siteCheck) {
//       if (t.indexOf(String(facts.siteCheck).toLowerCase()) < 0)
//         return 'missing the required site-check line: "' + facts.siteCheck + '"';
//       if (/\b(do not swim|don'?t swim|no[- ]swim|not a swim|stay out of the water)\b/.test(t))
//         return "gives an absolute no-swim call where the site check belongs";
//     }
//
// REPLACE WITH:
//
//     // Refused ALWAYS, not only when a site check exists to replace it. siteCheck
//     // is set by a Dangerous ENTRY alone, so 12 of the 64 label combinations are
//     // dangerous with none — a fifth of the danger space, previously unguarded.
//     if (/\b(do not swim|don'?t swim|no[- ]swim|not a swim|stay out of the water)\b/.test(t))
//       return "gives an absolute no-swim call (the bay is not shut)";
//     if (facts.siteCheck && t.indexOf(String(facts.siteCheck).toLowerCase()) < 0)
//       return 'missing the required site-check line: "' + facts.siteCheck + '"';
//
// ─────────────────────────────────────────────────────────────────────────────
// EDIT 5 — carry the third paragraph out and into the cache.
//
// 5a. Validate it too. This line appears TWICE (the draft and the retry) — change
//     both. FIND:
//
//     let clash = out && out.water_text ? summaryConflict(out.water_text, facts) : "no text";
//
// REPLACE:
//
//     let clash = out && out.water_text
//       ? summaryConflict(((out.water_text || "") + " " + (out.after_text || "")), facts)
//       : "no text";
//
//     WHY: mitigation and no-swim wording lands just as easily in the getting-out
//     paragraph — "wouldn't stay in long unless you're confident" is exactly that
//     shape. Validating water_text alone would leave the new paragraph unchecked.
//
// 5b. The cache write. FIND:
//         water_text: out.water_text,
//         facts,
//     REPLACE:
//         water_text: out.water_text,
//         after_text: out.after_text || "",
//         facts,
//
// 5c. The final response. FIND:
//       JSON.stringify({ wx_text: out.wx_text || "", water_text: out.water_text, cached: false }),
//     REPLACE:
//       JSON.stringify({ wx_text: out.wx_text || "", water_text: out.water_text, after_text: out.after_text || "", cached: false }),
//
// ─────────────────────────────────────────────────────────────────────────────
// BEFORE DEPLOYING — scheduled() branches on event.cron, so BOTH triggers must
// still be declared in Settings:
//     "0 18 * * *"   -> postDailySentence
//     the hourly one -> logHour + nsKeepTokenFresh
// Keep the RATE KV binding.
//
// AFTER DEPLOYING — summaries cache per date+clock-hour+timeCtx, so the new
// wording appears at the next hour bucket, not immediately.
