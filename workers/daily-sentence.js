// daily-sentence — MERGE into the bold-rain-6ded Worker (module format).
// 1) paste postDailySentence() + escHtml() + sydneyMMDD() into that Worker
// 2) add to its export default:  async scheduled(event, env, ctx) { ctx.waitUntil(postDailySentence(env)); },
// 3) in that Worker's wrangler.toml:  [triggers]  crons = ["0 18 * * *"]   # 04:00 AEST
// Secrets (your convention): env.SUPABASE_URL, env.SUPABASE_SERVICE_ROLE_KEY
//
// The daily-sentence sheet is keyed BY DATE, one row per calendar day:
//   col A = date "MM-DD"   col B = sentence   col C = title   col D = active (boolean)
// So the row is chosen by matching today's Sydney date to col A — NOT by cycling an
// index — and the announcement takes its title from col C and its body from col B.
const SHEET_ID = "1yUTCH1tpJ8HpyaLKlwqzQaZ8CEB-zDIW276zej5jcR0";
const DAILY_GID = "339561431";

function escHtml(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

// "MM-DD" in Sydney local time. The cron fires 18:00 UTC = 04:00 AEST, so at run
// time the Sydney calendar day is already the day we want to post for.
function sydneyMMDD(d){
  const p = new Intl.DateTimeFormat("en-AU", { timeZone: "Australia/Sydney", month: "2-digit", day: "2-digit" }).formatToParts(d);
  const mm = p.find(x => x.type === "month").value;
  const dd = p.find(x => x.type === "day").value;
  return mm + "-" + dd;
}

async function postDailySentence(env) {
  if (Date.now() < Date.parse("2026-07-23T04:00:00+10:00")) return;   // hold until 23 Jul 4am AEST

  const url = `https://docs.google.com/spreadsheets/d/${SHEET_ID}/gviz/tq?tqx=out:json&gid=${DAILY_GID}`;
  const raw = await (await fetch(url)).text();
  const json = JSON.parse(raw.replace(/^[^(]*\(/, "").replace(/\);?\s*$/, ""));
  const rows = (json.table.rows || [])
    .map(r => (r.c || []).map(c => (c && c.v != null) ? String(c.v).trim() : ""));

  // Match today's Sydney date in col A; only rows flagged active (col D == "true").
  const now = new Date();
  const key = sydneyMMDD(now);
  const pick = rows.find(r => r[0] === key && (r[3] || "").toLowerCase() === "true");
  if (!pick) return;                       // no row for today, or it's switched off

  const sentence = pick[1];                // col B
  const title    = pick[2] || "";          // col C
  if (!sentence) return;

  const ends = new Date(now.getTime() + 16 * 3600 * 1000);
  const SB = env.SUPABASE_URL, KEY = env.SUPABASE_SERVICE_ROLE_KEY;
  const h = { apikey: KEY, Authorization: "Bearer " + KEY, "Content-Type": "application/json" };

  await fetch(SB + "/rest/v1/announcements", {
    method: "POST",
    headers: { ...h, Prefer: "return=minimal" },
    body: JSON.stringify({
      app: "swim", quiet: true,
      title: title, body_html: escHtml(sentence),
      starts: now.toISOString(), ends: ends.toISOString()
    })
  });

  const cutoff = new Date(now.getTime() - 7 * 86400 * 1000).toISOString();
  await fetch(SB + "/rest/v1/announcements?quiet=eq.true&ends=lt." + encodeURIComponent(cutoff), {
    method: "DELETE", headers: h
  });
}
