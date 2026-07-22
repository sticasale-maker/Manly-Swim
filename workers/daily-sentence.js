// daily-sentence — record of what is LIVE in the bold-rain-6ded Worker (module
// format), captured 2026-07-23 from the deployed bundle so the repo matches what
// actually runs. If you re-merge from here:
//   1) paste postDailySentence() + sydneyMonthDay() + normMonthDay() into the Worker
//      (sbHeaders() already exists there; it uses env.SUPABASE_SERVICE_KEY)
//   2) in scheduled(): if (event.cron === "0 18 * * *") ctx.waitUntil(postDailySentence(env));
//   3) wrangler triggers must include BOTH crons: "0 18 * * *" (04:00 AEST, the
//      daily sentence) and the hourly logHour cron. A deploy that drops "0 18 * * *"
//      leaves this code correct but never fired.
//
// The daily-sentence sheet is keyed BY DATE, one row per calendar day:
//   col A = date "MM-DD"   col B = sentence   col C = title   col D = active (boolean)
// The row is chosen by matching today's Sydney date to col A; title comes from col C
// and the body from col B. Secrets: env.SUPABASE_URL, env.SUPABASE_SERVICE_KEY.
const DAILY_SHEET_ID = "1yUTCH1tpJ8HpyaLKlwqzQaZ8CEB-zDIW276zej5jcR0";
const DAILY_GID = "339561431";

// sbHeaders lives in the host Worker; reproduced here so this file stands alone.
function sbHeaders(env, extra) {
  return {
    "apikey": env.SUPABASE_SERVICE_KEY,
    "Authorization": "Bearer " + env.SUPABASE_SERVICE_KEY,
    "Content-Type": "application/json",
    ...extra || {}
  };
}

// "MM-DD" in Sydney local time. The cron fires 18:00 UTC = 04:00 AEST, so at run
// time the Sydney calendar day is already the day we want to post for.
function sydneyMonthDay(d) {
  const p = new Intl.DateTimeFormat("en-CA", { timeZone: "Australia/Sydney", month: "2-digit", day: "2-digit" }).formatToParts(d).reduce((a, x) => (a[x.type] = x.value, a), {});
  return `${p.month}-${p.day}`;
}

// Normalise a sheet date cell ("07-23", "7/23", "7-3") to zero-padded "MM-DD".
function normMonthDay(v) {
  const s = String(v == null ? "" : v).trim();
  const m = s.match(/^(\d{1,2})[-\/](\d{1,2})$/);
  if (!m) return "";
  return String(m[1]).padStart(2, "0") + "-" + String(m[2]).padStart(2, "0");
}

async function postDailySentence(env) {
  if (Date.now() < Date.parse("2026-07-23T04:00:00+10:00")) return;
  if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_KEY) return;
  let rows = [];
  try {
    const url = `https://docs.google.com/spreadsheets/d/${DAILY_SHEET_ID}/gviz/tq?tqx=out:json&gid=${DAILY_GID}`;
    const raw = await (await fetch(url)).text();
    const json = JSON.parse(raw.replace(/^[^(]*\(/, "").replace(/\);?\s*$/, ""));
    rows = (json.table.rows || []).map((r) => (r.c || []).map((c) => c && c.v != null ? String(c.v).trim() : ""));
  } catch (_) {
    return;
  }
  if (!rows.length) return;
  const today = sydneyMonthDay(new Date());
  const pick = rows.find((r) => normMonthDay(r[0]) === today && r[1] && (r[3] || "").toLowerCase() !== "false" && (r[3] || "").toLowerCase() !== "n");
  if (!pick) return;
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const now = new Date();
  const ends = new Date(now.getTime() + 16 * 3600 * 1e3);
  const SB = env.SUPABASE_URL;
  let sortOrder = 0;
  try {
    const sr = await fetch(`${SB}/rest/v1/announcements?app=eq.swim&select=sort_order&order=sort_order.asc&limit=1`, { headers: sbHeaders(env) });
    if (sr.ok) {
      const a = await sr.json();
      if (a.length && typeof a[0].sort_order === "number") sortOrder = a[0].sort_order - 1;
    }
  } catch (_) {
  }
  try {
    await fetch(`${SB}/rest/v1/announcements`, {
      method: "POST",
      headers: sbHeaders(env, { "Prefer": "return=minimal" }),
      body: JSON.stringify({
        app: "swim",
        quiet: true,
        sort_order: sortOrder,
        title: pick[2] || null,
        body_html: esc(pick[1]),
        starts_at: now.toISOString(),
        ends_at: ends.toISOString()
      })
    });
  } catch (_) {
  }
  try {
    const cutoff = new Date(now.getTime() - 7 * 864e5).toISOString();
    await fetch(`${SB}/rest/v1/announcements?quiet=eq.true&ends_at=lt.${encodeURIComponent(cutoff)}`, {
      method: "DELETE",
      headers: sbHeaders(env)
    });
  } catch (_) {
  }
}
