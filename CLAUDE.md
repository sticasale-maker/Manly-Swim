# Manly Swim — working rules

An ocean-swim forecast PWA for Cabbage Tree Bay / South Steyne, Manly NSW.
Live at `https://app.viz.net.au/Manly-Swim/`. Users are **regulars** — daily
swimmers, many for a decade. They do not need generic Manly information.

Almost everything is in `index.html` (~20k lines). Also: `bag-reports.html`
(stolen-bag admin), `guide.html`, `go.html`, `sw.js`, `migrations/*.sql`.

---

## 0. Start every session with an automation check (owner, 12 Nov 2026)

The owner opens sessions on the Drive folder `C:\Users\stica\Google Drive\VIZ\APPS\Manly swim`
(on the PC) or as a cloud session. **Before the first task, run the checks below and
open your first reply with a short "Automation check": one line per item, ✓ or what is
missing, and for anything missing the exact fix** (PowerShell for the PC: one command per
block, say which folder). Keep it to the list; do not re-explain items that pass.
Check, don't assume, and say "couldn't check" when a check is impossible from where you run.

On the PC (Drive folder session):
1. **Repo clones** — `C:\Users\stica\repos\Manly-Swim` and `C:\Users\stica\repos\swim-worker`
   exist, `git status` is clean, and `git fetch` shows them level with `origin/main`.
   Work happens in the clones, never in the Drive folder.
2. **This file in step** — `node tools/drive-check.js` from the Manly-Swim clone exits 0;
   if not, `node tools/drive-check.js --fix`. The Drive copy is what briefs sessions.
3. **Worker in step** — in swim-worker, `node tools/make-surge-module.cjs --check` passes,
   and `/bower-surge?debug=1`'s `model.hash` equals `SURGE_MODEL_HASH` in
   `src/surge-model.js`. If not: `npm run gen:surge`, commit, push, owner deploys.
4. **Scheduled tasks** (desktop app) — "Wall check on south-swell high-tide days" exists,
   its direction rule reads 140-210 deg, and it has the LOG step that appends to
   `wall-checks.csv` in the Drive folder.
5. **Surfline login** in the desktop app's browser pane (the wall check needs it).

In a cloud session:
1. **Repos** — both `sticasale-maker/manly-swim` and `sticasale-maker/swim-worker` are
   attached and cloned as siblings (`/home/user/Manly-Swim`, `/home/user/swim-worker`). If
   swim-worker is missing, attach it (`add_repo`, push) and clone it; say the owner can
   add it to the environment's Repositories so it comes by default.
2. **Environment** — the session runs on the "manly-swim" environment, and these answer:
   `gkspukabnfbzrvjoewpc.supabase.co` (history), `bold-rain-6ded.sticasale.workers.dev`,
   `middleman-to-sheet.sticasale.workers.dev`, `camstills.cdn-surfline.com`. A proxy 403
   on any of them means the wrong environment.
3. **Worker in step** — same as PC item 3, run in `/home/user/swim-worker`.
4. **Connectors** — Google Drive (to read `wall-checks.csv`) and Gmail. Say which are
   missing; the weekly review below cannot work without both.
5. **Scheduled task "Swim Manly wall-check weekly review"** (cloud, Sundays 09:46 Sydney) —
   exists, is enabled, and has the Google Drive and Gmail connectors plus email
   notifications (the owner sets those in claude.ai; a session cannot).
6. **What a cloud session cannot do**, stated once: deploy the Worker (`npx wrangler deploy`
   needs the owner's Cloudflare login on the PC), open the owner's PC folders (Remote
   Control folders are listed but not offered to cloud sessions on this account), see
   desktop-app scheduled tasks, or use the Surfline login.

---

## 1. Red zone means "which way in", not "the bay is shut"

A Dangerous entry off South Steyne does **not** mean the bay is closed. It means
try the sheltered ways in, and which one depends on surge. The exact sentence is
computed client-side as `facts.siteCheck` (`index.html`, search `siteCheck:`):

- Surge = Surgy or Washy → "check on site for entry at Shelly Beach"
- otherwise → "check on site for entry at Bower Lane or Shelly Beach"

**It is PROSE-ONLY now** (owner's ruling, 21 Sep 2026). The two standalone line
surfaces that printed it — under the answer-line headline, and on the tab ribbon —
were removed as a stutter under headlines that already name the beach. It has
exactly two carriers left, and between them they cover both states of the network:
the Oracle's water paragraph (the Worker validator forces the echo) and, when the
Worker is unreachable or its draft rejected, `generateBannerSummaryLocal()`, which
pushes the same `_sv.siteCheck` sentence into the local paragraph. **If you remove
or rewrite either carrier, the sentence needs a home first** — do not assume the
other one covers it, and do not restore a standalone line without saying why.

The Worker echoes that string **verbatim** and its validator **rejects** any
Oracle draft that omits it, and also rejects an absolute no-swim call. So:

- Never write copy that flattens the verdict to open/closed or "do not swim".
- Never offer **mitigation** — no "go with a mate", "fine if you're experienced",
  "wait two hours and it'll be better". That is the actual banned category.
  Reassurance is the same thing: no "manageable", "nothing you can't handle",
  "not a problem" about surge, drift, the point, swell or entry — on any day, not
  only dangerous ones. Both validators reject it.
- **Avoidance advice is not mitigation** (owner's ruling, 13 Sep 2026): "give it a
  wide berth", "stay away from the rocks" are allowed — an action with no
  promised result. The point line (`facts.lineCheck`, search `lineCheck:`) is
  "the point is drifty|surgy|washy — give it a wide berth". Never add a promise or
  a condition to it, and never say the point is **breaking** — that has not been
  observed, only the surge its shape creates.
- Any new surface showing a verdict must consume `_bannerFacts`, never re-derive
  it. The rule already lives in four places (local baseline, client
  `summaryConflict`, Worker `SWIMSUM_SYS`, Worker validator) that must stay in
  step; a fifth that re-derives will drift silently. The tab ribbon (`#tabRibbon`,
  on Bay and Community) is the model to copy: it only reads `_bannerFacts` and
  repaints on the `bannerfacts` event dispatched right after the facts are built.
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

## 6. The Cloudflare Worker is shared, and lives in its own repo

`bold-rain-6ded` also serves the Forecast App. Since 19 Sep 2026 its source is
`C:/Users/stica/repos/swim-worker`: local git, private, one file per feature. It is
deployed with `npx wrangler deploy` from there, and **never edited in the dashboard**
again, because the next deploy overwrites a dashboard edit. Its README maps the files
and its CLAUDE.md holds the rules. The Drive `worker-*.js` copies are retired.

- Never copy any of it into this repo: the source holds API keys, and this repo is
  public and served by GitHub Pages.
- `npm test` there must pass before a deploy. It proves a refactor changed nothing, and
  it shows exactly what a deliberate change altered.
- New routes must be **additive**; never change `/forecast` defaults.
- **Both** crons and the RATE KV binding are declared in its `wrangler.toml`, so a
  deploy cannot drop them. A 30 Jul 2026 dashboard deploy stripped the daily cron
  and nobody noticed for two days; never remove them from the toml.
- Free-tier KV write cap is real: cache at the edge, not in KV, for hot paths.
- **The Worker's Surge is generated from this file** (11 Nov 2026). `/bower-surge` and the
  Forecast App's CTB score run `blSwimWater` and its helpers lifted verbatim into
  swim-worker's `src/surge-model.js`. After any change to the Surge code (`blSwimWater`,
  `surgeSouthWrap`, `pointSurge`, ...) or `SITE_DEFAULTS`, push here, then in swim-worker
  run `npm run gen:surge`, `npm test`, and deploy when the owner says go. Until then the
  Worker scores the old Surge, which is how 11 Nov's Steady-vs-Drifty happened. Live
  `/tune` values reach the Worker by themselves.
- **Surge reads no sheet** (owner, 11 Nov 2026). The BL table, `blWaveSens`, the Surge
  band edges, `tideGain` and `tideRef` live in `SITE_DEFAULTS`; the New NS tab still holds
  them but the app ignores those cells (Entry and Chop still read theirs). Never wire one
  back to the sheet: the Worker's generator refuses to run if you do, because the Worker
  could not follow a sheet edit.
- A deploy from this app session is blocked by the permission classifier unless the
  owner allows `npx wrangler deploy`; otherwise the owner runs it after saying go.
- **Cloud sessions attach swim-worker too** (owner, 12 Nov 2026). It is on GitHub as
  the private `sticasale-maker/swim-worker`. If a cloud session working on this app does
  not have it, attach it with `add_repo` (push access) and clone it to a sibling of this
  repo (`/home/user/swim-worker` beside `/home/user/Manly-Swim`), because its generator
  reads `../Manly-Swim/index.html`. Then after a Surge or `SITE_DEFAULTS` push here, run
  `npm run gen:surge` there, check `node test/surge-fixtures.mjs`, commit
  `src/surge-model.js` and push. The owner only runs `npx wrangler deploy` on the PC.
  Commit the regenerated file every time: on 11 Nov one was deployed from the PC and never
  pushed, which left GitHub's `main` behind the live Worker.

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
- **The scorer fails closed** (30 Sep 2026). An hour with no NS data gets
  `noNearshoreScore()` from `calcScore`: cls `none`, grey on every surface, "No
  call" on the answer line. It is never scored from the WW backup instead, which
  once put a confident 72 red or 3.5 green on the same hour. `?src=ww` still scores
  WW on purpose, and a real NS outage goes to vecchio.html.
- **One announced exception: the WillyWeather backup** (owner, 5 Oct 2026). When
  NSW's run is more than 48 h old (`_nsStaleSince`), every forecast hour scores from
  WW (`wwPath` in `calcScore`) under the owner's brown banner. The switch covers the
  whole forecast, never a stray row, and logged history keeps its NS scores. WW's
  3-hourly gap hours stay grey. The NS-modelled heights are hidden (`hasNsWaves`).
  The Worker writes the Oracle from `backup: true` facts, in its own `:ww` cache
  bucket. Its prompt rule 6 names the backup, and its validator rejects any sizing
  of the entry, because there is no ramp figure. The local backup paragraph stands
  in until it answers. Backup mode ends by itself on the first fresh run. The
  Worker's hourly cron (`src/ns-stale.js`) learns NSW's publishing cadence. It emails
  the owner within the hour once a run is overdue, then once a day, then once when a
  fresh run arrives. The backup's Entry has **its own calibration** (owner's choice,
  5 Oct 2026, from three camera calls). It has three parts:
  - swell x`wwSwellMult` 0.7;
  - the tide it had before the 14 Sep NSW retune (`wwTideGain` 0.39, `tideGateWW` 70-90
    deg);
  - a curve, `wwEntryCurve` (`wwEntryLowMult` 0.79), that eases small and moderate
    readings and leaves everything at or above the Dangerous edge exactly as it was.

  Its Surge (`bowerSurgeWater`) was brought back in proportion the same way. The bay
  stack goes through `wwSurgeCurve` (`wwSurgeLowMult` 0.15, pinned at the live Washy
  edge), the point is untouched, and the bands use NSW's live `surgeThresholds`. That
  matches NSW, where Surge never sits above Entry.

  All of it is read only on WW inputs (`useWWInputs`), and NS hours stay
  byte-identical. Never point the backup back at NSW's tide knobs: the 14 Sep gate and
  gain were tuned for NSW's bay-mouth sea, not WillyWeather's offshore swell.

## 9. Offline-first

The app must render on a dead connection in a car park. `sw.js` pre-caches the
shell; API hosts are passthrough. Media is not intercepted — `<video>` needs
HTTP Range/206 and a network-first SW breaks playback on iOS Safari. The splash
video is the one exception and serves ranges by hand (`serveSplashVideo`).

The forecast itself renders offline from the phone's saved copy: `loadWwCache()`
for the WillyWeather rows and `mergeSavedNs()` for the NS feed, up to
`SAVED_FORECAST_MAX_MS` (48 h) old. Once it is more than 2 h old, the "SAVED
FORECAST" note above the rails gives its age. Until 30 Sep 2026 the copy was saved
in one shape and read in another, so none of this ever ran. Keep one reader per
saved copy.

---

## Current state worth knowing

- **The page is split into bottom tabs** (Today · Bay · Community · More, since
  13 Sep 2026). Cards never move in the DOM: each top-level card in
  `#panel-snapshot` carries `data-tab="today|bay|community"` and CSS on
  `body.tabs-on[data-tab]` hides the other tabs' cards. So:
  - **Every new top-level card needs a `data-tab`**, or it shows on every tab.
  - Anything that scrolls to a card must call `window.showTabFor(el)` first, or
    it scrolls to an element that is `display:none`.
  - A card that measures itself (canvas, map) is sized while hidden; tab switches
    dispatch `resize`, so re-fit on that.
  - More holds settings and reference pages only — never a forecast or community
    section. Hiding a section stays the job of Choose sections.
  - Tabs are off with `?tune=1`, `?backtest=1` and `?debug=1`.

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

**The owner runs commands in Windows PowerShell 5.1** (owner, 11 Nov 2026). Every command
you give them must work there: no `&&` or `||` (one command per block instead), no
bash-only syntax (`export`, `$(...)`, heredocs, `grep`), Windows paths, and say which
folder to `cd` into first. Fence the blocks as `powershell`.


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
