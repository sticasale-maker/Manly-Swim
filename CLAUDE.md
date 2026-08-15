# Manly Swim — working rules

An ocean-swim forecast PWA for Cabbage Tree Bay / South Steyne, Manly NSW.
Live at `https://app.viz.net.au/Manly-Swim/`. Users are **regulars** — daily
swimmers, many for a decade. They do not need generic Manly information.

Almost everything is in `index.html` (~20k lines). Also: `bag-reports.html`
(stolen-bag admin), `guide.html`, `go.html`, `sw.js`, `migrations/*.sql`.

---

## 1. Red zone means "which way in", not "the bay is shut"

A Dangerous entry off South Steyne does **not** mean the bay is closed. It means
try the sheltered ways in, and which one depends on surge. The exact sentence is
computed client-side as `facts.siteCheck` (`index.html`, search `siteCheck:`):

- Surge = Washy → "check on site for entry at Shelly Beach"
- otherwise → "check on site for entry at Bower Lane or Shelly Beach"

The Worker echoes that string **verbatim** and its validator **rejects** any
Oracle draft that omits it, and also rejects an absolute no-swim call. So:

- Never write copy that flattens the verdict to open/closed or "do not swim".
- Never offer **mitigation** — no "go with a mate", "fine if you're experienced",
  "wait two hours and it'll be better". That is the actual banned category.
- Any new surface showing a verdict must consume `_bannerFacts`, never re-derive
  it. The rule already lives in four places (local baseline, client
  `summaryConflict`, Worker `SWIMSUM_SYS`, Worker validator) that must stay in
  step; a fifth that re-derives will drift silently.
- Features that could read as encouragement (streaks, "better than yesterday",
  group roll-calls) must be hard-suppressed when `safetyLevel === 'dangerous'`.

## 2. Never state a derived in-bay height as a measurement

Modelled ramp/bay residual heights are scoring quantities, not something a
swimmer can see. Two heights may go to the summariser: the **offshore** swell and
the **ramp residual** at the entry point. `bayResidualH` is withheld deliberately.

Prose must never quote a modelled in-bay height — that is what drew the "makes
the app look ridiculous" complaint. (The swell *card* still shows both heights as
a labelled pair; that was a deliberate exception, not a licence for prose.)

## 3. No descriptions of people, anywhere

The stolen-bag database has no suspect field and never will. Crowd-sourced
descriptions of individuals are a defamation and racial-profiling problem and
would get the dataset dismissed. This extends to any found-property or community
board: objects and fixtures only, never "did anyone see who".

## 4. No accounts

Identity is a per-device UUID and a pseudonym. There is no login and no user
profile, and adding one drags in a privacy notice and a support burden.

Nuance: this is about **identity**, not telemetry. A device UUID plus
`view_mode`/`rail_style` already go to Supabase, and community features already
write user content server-side. Personal *preferences* (habit hours, thresholds)
stay on-device in `localStorage`, mirrored to a 400-day cookie via
`persistGet`/`persistSet` because Safari ITP evicts localStorage after 7 idle days.

## 5. Never hand-edit the build stamps

CI stamps both on every push to `main`:

- `var APP_BUILD = '…'` in `index.html`
- `const CACHE_VERSION = '…'` in `sw.js`

Before committing, this must be empty:

```bash
git diff HEAD | grep -E '^[+-].*(APP_BUILD|CACHE_VERSION)'
```

## 6. The Cloudflare Worker is shared and dashboard-managed

`bold-rain-6ded` also serves the Forecast App. It is edited in the dashboard, not
deployed from this repo.

- New routes must be **additive**; never change `/forecast` defaults.
- Any redeploy must re-declare **both** crons — a 30 Jul 2026 deploy silently
  stripped the daily-sentence cron and it went unnoticed for two days.
- Keep the KV binding, or `/tune` silently falls back to cache.
- Free-tier KV write cap is real: cache at the edge, not in KV, for hot paths.

## 7. Bake tuned knobs into `SITE` defaults

Whenever a LOCAL tune knob changes, write the value into the `SITE` defaults in
`index.html` and push. Never leave a tuned value living only in Worker KV.

## 8. Absent data is grey, never green

A missing reading must never be coloured as a good one (`.bbf-day-none` is the
reference treatment). Related, and just as binding:

- **A model must never visually outrank a real observation.** A forecast cannot
  sit above a photo-backed sighting or a community report.
- **Nothing undated.** If a number can go stale, show its age and fade it. The
  sea-temp tile shipped for five days showing an 11 Aug reading as if it were
  current, because the date was fetched and discarded.

## 9. Offline-first

The app must render on a dead connection in a car park. `sw.js` pre-caches the
shell; API hosts are passthrough. Media is not intercepted — `<video>` needs
HTTP Range/206 and a network-first SW breaks playback on iOS Safari. The splash
video is the one exception and serves ranges by hand (`serveSplashVideo`).

---

## Current state worth knowing

- **Live scoring path is NS.** `const DEFAULT_SRC = 'ns'`. (An older note claiming
  the default flipped to WW is stale — verify in code, not from notes.)
- **Sea temperature has one source: a Facebook post scraper** on a laptop, outside
  this repo. `index.html` states the community reading is the ONLY source and
  WW/Open-Meteo is never used. It is the most fragile thing in the app; a labelled
  model fallback is a legitimate conversation but must not be added silently.
- **A re-tune is owed.** The WW wind location moved from Long Reef to Cabbage Tree
  Bay on 13 Aug 2026; every wind-driven knob was tuned on wind ~5 km/h stronger.
  Anything touching CHOP is building on knobs known to be miscalibrated.

## Editing and deploying

The working folder is a **Google-Drive-synced copy, not a git repo**. Never
`git init` here — Drive corrupts `.git`.

Work from a clone outside Drive, and **never copy a whole file in either
direction**. The two copies drift both ways, and a wholesale copy destroys work:

- Drive can be **behind** the repo (it has been missing the SW push handlers and
  the vecchio failover).
- Drive can be **ahead** with another session's unpushed work.
- Drive's `APP_BUILD` / `CACHE_VERSION` are always stale, because CI stamps them
  in the repo and that never syncs back.

So: diff the Drive file against `HEAD`, apply **only** the intended hunks onto
`HEAD`, and confirm with `git diff --cached --stat HEAD` that nothing else rode
along. Push to `main`; GitHub Pages serves it at `app.viz.net.au/Manly-Swim/`
(every repo on this account inherits that domain — no per-repo DNS).

A push is not a deploy. Verify the live `APP_BUILD` actually changed; the Fastly
edge caches for ~10 minutes, so cache-bust with `?cb=` when checking.
