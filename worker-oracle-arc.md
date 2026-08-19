# Worker paste — the swimmer's arc

Five edits to `/swim-summary` in **bold-rain-6ded** (Cloudflare dashboard editor),
plus one SQL column. Every block below is literal: copy the FIND, copy the
REPLACE, no stripping.

Supersedes `worker-summary-chop-and-noswim.js` — its two edits are 4 and 5 here.

Client side is already deployed and sending `seaTemp`, `arrival` and `exit`. The
Worker builds `facts` from an explicit field list, so it drops all three until
edit 2 lands. That is why nothing has changed on screen yet.

---

## SQL first

Run in the Supabase SQL editor. Do this **before** deploying, or the cache write
in 5b fails.

```sql
alter table public.conditions_summary
  add column if not exists after_text text;
```

Separately, the water-temp date trigger (`migrations/water_temp_stamp_day.sql`)
so the `day` cell stops needing to be updated by hand.

---

## Edit 1 — cache read

Two changes on one statement.

FIND

```js
&select=wx_text,water_text`,
```

REPLACE

```js
&select=wx_text,water_text,after_text`,
```

FIND

```js
              JSON.stringify({ wx_text: rows[0].wx_text, water_text: rows[0].water_text, cached: true }),
```

REPLACE

```js
              JSON.stringify({ wx_text: rows[0].wx_text, water_text: rows[0].water_text, after_text: rows[0].after_text || "", cached: true }),
```

---

## Edit 2a — new helper

Paste at top level, after `numClamp`.

```js
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
```

---

## Edit 2b — the facts object

FIND

```js
          shelteredContrast: !!wa.shelteredContrast
        }
      };
```

REPLACE

```js
          shelteredContrast: !!wa.shelteredContrast
        },
        seaTemp: (f.seaTemp && numClamp(f.seaTemp.c, 5, 32, null) != null)
          ? { c: numClamp(f.seaTemp.c, 5, 32, null), ageDays: numClamp(f.seaTemp.ageDays, 0, 30, 0) }
          : null,
        arrival: shapeMoment(f.arrival),
        exit: shapeMoment(f.exit)
      };
```

`seaTemp` is absent unless the client sent a reading under two days old — one
Facebook post is the only source, so a stale one is withheld rather than shrugged
in. `ageDays` travels with it so the prose can date it out loud.

---

## Edit 3 — the prompt

Open `oracle-prompt.txt`, select all, copy.

In the Worker, find `var SWIMSUM_SYS = ` followed by a backtick. Select from just
**after** that opening backtick down to the closing backtick before the `;`, and
paste over it. Keep both backticks and the semicolon.

This is the risky edit. The other four are mechanical; this one replaces the whole
instruction set. If the model starts returning malformed JSON or tripping the
validator, it is here. A validator failure is safe — 502, and the app falls back
to local prose — so the failure mode is no summary rather than a wrong one.

---

## Edit 4 — the no-swim guard

FIND

```js
    if (facts.siteCheck) {
      if (t.indexOf(String(facts.siteCheck).toLowerCase()) < 0)
        return 'missing the required site-check line: "' + facts.siteCheck + '"';
      if (/\b(do not swim|don'?t swim|no[- ]swim|not a swim|stay out of the water)\b/.test(t))
        return "gives an absolute no-swim call where the site check belongs";
    }
```

REPLACE

```js
    if (/\b(do not swim|don'?t swim|no[- ]swim|not a swim|stay out of the water)\b/.test(t))
      return "gives an absolute no-swim call (the bay is not shut)";
    if (facts.siteCheck && t.indexOf(String(facts.siteCheck).toLowerCase()) < 0)
      return 'missing the required site-check line: "' + facts.siteCheck + '"';
```

`siteCheck` is set by a Dangerous ENTRY alone, so 12 of the 64 label combinations
are dangerous with none — a fifth of the danger space that had no guard at all.

---

## Edit 5a — validation, TWICE

Do these one at a time, not find-and-replace-all: the second is a bare assignment
without `let`, and blindly replacing both produces two `let clash` in one scope
and the Worker will not parse.

FIRST occurrence — FIND

```js
        let clash = out && out.water_text ? summaryConflict(out.water_text, facts) : "no text";
```

REPLACE

```js
        let clash = out && out.water_text
          ? summaryConflict(((out.water_text || "") + " " + (out.after_text || "")), facts)
          : "no text";
```

SECOND occurrence (inside the retry) — FIND

```js
          clash = out && out.water_text ? summaryConflict(out.water_text, facts) : "no text";
```

REPLACE

```js
          clash = out && out.water_text
            ? summaryConflict(((out.water_text || "") + " " + (out.after_text || "")), facts)
            : "no text";
```

Mitigation wording lands just as easily in the getting-out paragraph — "wouldn't
stay in long unless you're confident" is exactly that shape — so the new
paragraph has to be validated too.

---

## Edit 5b — cache write

FIND

```js
              water_text: out.water_text,
              facts,
```

REPLACE

```js
              water_text: out.water_text,
              after_text: out.after_text || "",
              facts,
```

---

## Edit 5c — the response

FIND

```js
        JSON.stringify({ wx_text: out.wx_text || "", water_text: out.water_text, cached: false }),
```

REPLACE

```js
        JSON.stringify({ wx_text: out.wx_text || "", water_text: out.water_text, after_text: out.after_text || "", cached: false }),
```

---

## Before you hit Deploy

Settings must still list **both** cron triggers — `0 18 * * *` for
`postDailySentence`, and the hourly one for `logHour` + `nsKeepTokenFresh` — and
the **RATE** KV binding.

Summaries cache per date + clock-hour + timeCtx, so the new wording appears at the
next hour bucket, not immediately.
