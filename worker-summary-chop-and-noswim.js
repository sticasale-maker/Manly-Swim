// ─────────────────────────────────────────────────────────────────────────────
// PASTE JOB — bold-rain-6ded, Cloudflare dashboard editor. NOT deployed from this
// repo (CLAUDE.md §6). Two edits to the /swim-summary path, both additive in
// behaviour: no route defaults change, no new binding, no new secret.
//
//   EDIT 1  SWIMSUM_SYS  — chop gets the agreed comfort wording
//   EDIT 2  summaryConflict — the no-swim guard stops being conditional
//
// Both have already landed on the client side (index.html). The Worker holds the
// authoritative copy of each: SWIMSUM_SYS is what the model is actually told, and
// summaryConflict is what rejects a draft BEFORE it is cached. A bad summary that
// gets past the Worker sticks for the whole clock-hour bucket, so the client copy
// is the belt and this is the braces.
//
// ─────────────────────────────────────────────────────────────────────────────
// EDIT 1 — inside the SWIMSUM_SYS template literal.
//
// Chop no longer sets the safety verdict (it is comfort, not safety), which makes
// it MORE important that the prose still describes it — otherwise removing it from
// the verdict quietly removes it from the summary too.
//
// FIND this line (in the "SURGE and CHOP act across the WHOLE bay" block):
//
//     - If the swim verdict is "Choppy" or "Messy", say so and attribute the chop to the wind coming from windDir.
//
// REPLACE WITH:
//
//     - Chop is about COMFORT, not safety. Never call a day dangerous because of it, and never leave it out either. Word it by the verdict:
//       "Smooth" -> say the surface is smooth.
//       "Ripply" -> say the surface is ripply.
//       "Choppy" or "Messy" -> say the surface is choppy/messy AND that swim and breathing rhythm may be uncomfortable, attributing the chop to the wind coming from windDir.
//
// ─────────────────────────────────────────────────────────────────────────────
// EDIT 2 — in summaryConflict(), the dangerous-day block.
//
// The no-swim guard sits INSIDE `if (facts.siteCheck)`, and siteCheck is only set
// when ENTRY is Dangerous. 12 of the 64 label combinations are dangerous WITHOUT a
// site check (Washy or Surgy surge, entry not Dangerous) — a fifth of the danger
// space with no guard at all. That is the gap "The water is dangerous tomorrow. Do
// not swim." came through on 20 Aug 2026.
//
// FIND:
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
//     // Refused ALWAYS, not only when a site check exists to replace it. A hard
//     // entry off South Steyne never means the bay is shut (CLAUDE.md §1).
//     if (/\b(do not swim|don'?t swim|no[- ]swim|not a swim|stay out of the water)\b/.test(t))
//       return "gives an absolute no-swim call (the bay is not shut)";
//     if (facts.siteCheck && t.indexOf(String(facts.siteCheck).toLowerCase()) < 0)
//       return 'missing the required site-check line: "' + facts.siteCheck + '"';
//
// ─────────────────────────────────────────────────────────────────────────────
// NOT NEEDED, worth knowing why: the siteCheck WHITELIST in the /swim-summary
// handler stays exactly as it is. The client now picks "Shelly Beach" for Surgy as
// well as Washy, but both sentences were already in the whitelist — only which one
// is chosen changed. Reword either string on the client and the whitelist silently
// turns it into null and the day loses its site check entirely.
//
// ─────────────────────────────────────────────────────────────────────────────
// BEFORE DEPLOYING — scheduled() branches on event.cron, so BOTH triggers must
// still be declared in Settings:
//     "0 18 * * *"   -> postDailySentence
//     the hourly one -> logHour + nsKeepTokenFresh
// A 30 Jul 2026 deploy silently stripped the daily-sentence cron and it went
// unnoticed for two days. Keep the RATE KV binding too.
//
// AFTER DEPLOYING — cached summaries are keyed date+clock-hour+timeCtx, so an
// already-cached draft survives until the hour rolls. To see the new wording
// immediately, wait for the next hour bucket.
