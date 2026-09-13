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

- Surge = Surgy or Washy → "check on site for entry at Shelly Beach"
- otherwise → "check on site for entry at Bower Lane or Shelly Beach"

The Worker echoes that string **verbatim** and its validator **rejects** any
Oracle draft that omits it, and also rejects an absolute no-swim call. So:

- Never write copy that flattens the verdict to open/closed or "do not swim".
- Never offer **mitigation** — no "go with a mate", "fine if you're experienced",
  "wait two hours and it'll be better". That is the actual banned category.
  Reassurance is the same thing: no "manageable", "nothing you can't handle",
  "not a problem" about surge, drift, the point, swell or entry — on any day, not
  only dangerous ones. Both validators reject it.
- The point line (`facts.lineCheck`, search `lineCheck:`) is "the point is
  drifty|surgy|washy — keep your line off it", worded by the owner on 13 Sep 2026.
  An action with no promised result is allowed; never add a promise or a
  condition to it, and never say the point is **breaking** — that has not been
  observed, only the surge its shape creates.
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

A missing reading must never be coloured as a good one. The live reference
treatment is `.bbf-chart-empty` (neutral blue-grey fill, `--dim` ink), rendered
when the bluebottle wind forecast is unavailable. The older `.bbf-day-none` tile
class this rule used to cite is now orphaned CSS — the per-day tiles it styled
were replaced by the 4-hourly risk arc, so don't copy it as a pattern. Related,
and just as binding:

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
- **Sea temperature is community-first, with a labelled satellite fallback.** The
  primary source is a Facebook post scraper on a laptop, outside this repo, whose
  reading Marco enters into `water_temp_now` by hand each morning. That is still the
  most fragile thing in the app. The fallback was agreed on 20 Aug 2026 and is the
  conversation the old note here reserved — it is **not** a licence for more:
  - One day old counts as CURRENT (it just predates that morning's entry).
  - At **two** days the app switches to Open-Meteo marine SST and **says so** — the
    tile shows a "satellite" label and the Oracle names it. Never swap silently: a
    regular knows the community number and would see it change for no stated reason.
  - `resolveSeaTemp()` is the ONE decider, read by both the tile and the Oracle's
    facts. A second caller deciding this itself is how the card and the prose end up
    quoting different temperatures.
  - The satellite is a good LEVEL and a poor trend (18.0 at 06:00 on eight straight
    days in Aug 2026, 0.2 off the community figure). Fine for a gap; not a
    measurement, and never to be presented as one.
- **A re-tune is owed.** The WW wind location moved from Long Reef to Cabbage Tree
  Bay on 13 Aug 2026; every wind-driven knob was tuned on wind ~5 km/h stronger.
  Anything touching CHOP is building on knobs known to be miscalibrated.

## Editing and deploying

The working folder is a **Google-Drive-synced copy, not a git repo**. Never
`git init` here — Drive corrupts `.git`.

**There are two copies of THIS file, and the Drive one is the one that gets
loaded as a session's instructions.** So a stale Drive copy does not sit there
harmlessly — it briefs the next session with rules the code no longer follows.
On 12 Sep 2026 it was 26 days behind and briefed a session with the pre-20-Aug
`siteCheck` rule while that session was fixing exactly that drift in `index.html`.

```bash
node tools/drive-check.js          # check — exit 1 on CLAUDE.md drift
node tools/drive-check.js --fix    # write the committed CLAUDE.md across
```

It also reports on the Drive `index.html`, but as a **warning only** — never
fixed, and it never changes the exit code. That file's `APP_BUILD` is stamped by
CI and never syncs back, so the copies always differ and a gate there would cry
wolf every run. Lines it reports as present only in Drive are usually code
deliberately RETIRED from the repo, not unpushed work — on 12 Sep 2026 all of
them were the calibration logger, the freeze rig and the camera helpers, whose
tables have since been dropped. Read them before assuming either.

Run the check **at the start of a session** and after any pull that moved this
file. A `post-commit` hook syncs Drive automatically whenever a commit touches
CLAUDE.md, but it cannot cover everything: `reset --hard` fires no hook, so a
change pushed from another machine arrives silently. Enable the hook once per
clone with `git config core.hooksPath tools/hooks`.

Work from a clone outside Drive. **The repo is the source of truth, not Drive.**
Start every change from `HEAD`, then apply your edits onto it.

**The clone is shared, so `reset --hard` is not a safe opener.** Sessions run
concurrently in `C:/Users/stica/repos/Manly-Swim`, and `reset --hard` is aimed at
whatever the other one has uncommitted. Check before you reset, every time:

```bash
git fetch origin
git status --porcelain --untracked-files=no   # MUST print nothing
git reset --hard origin/main                  # only if it did
```

If it is dirty and the changes are not yours, do **not** reset — ask via
`SendMessage` who owns them, then wait. `git worktree list` does **not** cover
this: it shows spawned task sessions, not a peer editing the main clone directly.
On 12 Sep 2026 two sessions were rewriting the same safety copy minutes apart, and
that `git status` line was the only thing between the second and the loss of the
first's work. When you are the one holding uncommitted edits, commit them before
you hand the clone over or go idle — do not leave them in the working tree.

**A clean tree is a snapshot, not a lease.** That check guards the instant you
reset and nothing after it. On 12 Sep 2026 a session checked clean, edited one
file, and minutes later found a *third* session's uncommitted work sitting beside
its own. So stage by path — `git add index.html`, never `git add -A` or
`git commit -a` — and read `git diff --cached --stat` before every commit. A file
you did not touch appearing there is a peer in the clone, not a slip in your edit:
unstage it, leave it alone, and say so. `ListAgents` names who is live and
`SendMessage` reaches them.

**Never copy a whole file from Drive into the clone** —
the two copies drift both ways and a wholesale copy destroys work:

- Drive can be **behind** the repo. `sw.js` habitually is: a wholesale copy has
  twice deleted the push-notification handlers, `notificationclick` and the
  vecchio failover, and on 17 Aug 2026 that actually reached `main`.
- Drive can be **ahead** with another session's unpushed work.
- Drive's `APP_BUILD` / `CACHE_VERSION` are always stale, because CI stamps them
  in the repo and that never syncs back.

So: diff the Drive file against `HEAD`, apply **only** the intended hunks onto
`HEAD`, and gate on the size of the result.

```bash
git diff --stat HEAD        # must match the change you intended
git diff HEAD | grep -E '^[+-].*(APP_BUILD|CACHE_VERSION)'   # must be empty
```

**If the diff is bigger than the edit you made, stop and read it.** 63 changed
lines for a 5-line edit is the tell, and it is the check that would have caught
every one of the incidents above. The same applies after any scripted deletion:
confirm it took only what you meant (e.g. compare the `function` names in the
touched module before and after) — one over-long slice removed `startSwellMirror`
along with its neighbour and blanked the dashboard's swell map.

Push to `main`; GitHub Pages serves it at `app.viz.net.au/Manly-Swim/` (every repo
on this account inherits that domain — no per-repo DNS). Another session may push
while you work: if `git push` is rejected, `git fetch && git rebase origin/main`,
re-check syntax, then push.

A push is not a deploy. Verify the live `APP_BUILD` actually changed; the Fastly
edge caches for ~10 minutes, so cache-bust with `?cb=` when checking.
