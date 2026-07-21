// daily-sentence — MERGE into the bold-rain-6ded Worker (module format).
// 1) paste postDailySentence() + escHtml() into that Worker
// 2) add to its export default:  async scheduled(event, env, ctx) { ctx.waitUntil(postDailySentence(env)); },
// 3) in that Worker's wrangler.toml:  [triggers]  crons = ["0 18 * * *"]   # 04:00 AEST
// Secrets (your convention): env.SUPABASE_URL, env.SUPABASE_SERVICE_ROLE_KEY
const SHEET_ID = "1yUTCH1tpJ8HpyaLKlwqzQaZ8CEB-zDIW276zej5jcR0";
const DAILY_GID = "339561431";

function escHtml(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

async function postDailySentence(env) {
  if (Date.now() < Date.parse("2026-07-23T04:00:00+10:00")) return;   // hold until 23 Jul 4am AEST

  const url = `https://docs.google.com/spreadsheets/d/${SHEET_ID}/gviz/tq?tqx=out:json&gid=${DAILY_GID}`;
  const raw = await (await fetch(url)).text();
  const json = JSON.parse(raw.replace(/^[^(]*\(/, "").replace(/\);?\s*$/, ""));
  const rows = (json.table.rows || [])
    .map(r => (r.c || []).map(c => (c && c.v != null) ? String(c.v).trim() : ""))
    .filter(r => r[0] && r[0].toLowerCase() !== "sentence" && (r[2] || "").toLowerCase() !== "n");
  if (!rows.length) return;

  const pick = rows[Math.floor(Date.now() / 86400000) % rows.length];
  const now = new Date();
  const ends = new Date(now.getTime() + 16 * 3600 * 1000);

  const SB = env.SUPABASE_URL, KEY = env.SUPABASE_SERVICE_ROLE_KEY;
  const h = { apikey: KEY, Authorization: "Bearer " + KEY, "Content-Type": "application/json" };

  await fetch(SB + "/rest/v1/announcements", {
    method: "POST",
    headers: { ...h, Prefer: "return=minimal" },
    body: JSON.stringify({
      app: "swim", quiet: true,
      title: pick[1] || "", body_html: escHtml(pick[0]),
      starts: now.toISOString(), ends: ends.toISOString()
    })
  });

  const cutoff = new Date(now.getTime() - 7 * 86400 * 1000).toISOString();
  await fetch(SB + "/rest/v1/announcements?quiet=eq.true&ends=lt." + encodeURIComponent(cutoff), {
    method: "DELETE", headers: h
  });
}
