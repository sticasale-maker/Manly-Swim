# Supabase backup

`supabase_backup.py` takes a snapshot of the Supabase project (`gkspukabnfbzrvjoewpc`)
into Google Drive. It exists so the project can sit on the **Free** plan, which has
no daily backups and no point-in-time recovery.

## Do you actually need it?

Be honest about this before scheduling anything. Nothing about the forecast depends
on the database — scoring is client-side, fed by the Worker and the NS/WW feeds. If
the whole database vanished the app would still open and still give a verdict.

What is genuinely irreplaceable:

| Table | If it were lost |
|---|---|
| `bag_theft_reports` | **The real one.** Evidence people submitted once, behind the police pack. Not reconstructable. |
| community content | 24 intros, Bay Talk posts and replies, sightings, bluebottle reports, plus photos. A shame, not a disaster. |
| `forecast_history` | 60 days of recorded conditions. Rebuilds at ~24 rows/day. |
| everything else | Regenerates on its own. The sea-temp tile refills on the scraper's next run. |

And the realistic risk is not Supabase losing data — it is **us** breaking something:
a bad migration, a delete without a where clause, a drop on the wrong table. On Free
there is no undo for that.

So: printing the police pack from `bag-reports.html` when reports come in already
covers the part that matters. This script is the belt to that braces, and it is
useful run by hand even if it is never scheduled.

## One-time setup

1. Supabase dashboard → Project Settings → API Keys → copy the **service_role** key.
2. Save it to `C:\Users\stica\.viz-secrets\supabase_service_key.txt` (create the
   folder). That path is **outside Google Drive on purpose** — the key must not sync
   to the cloud. It is also outside the repo, and nothing here ever prints it.
3. Run it once by hand:

   ```
   python C:\Users\stica\repos\Manly-Swim\tools\supabase_backup.py
   ```

The service_role key is required, not a nicety: the public key cannot read
`bag_theft_reports`, and a backup that quietly skips the evidence table is worse
than no backup. **The script aborts if the key it finds cannot read it.**

## Scheduling it (optional)

```
schtasks /create /tn "Manly Swim Supabase backup" /sc daily /st 03:30 /f ^
  /tr "C:\Users\stica\repos\Manly-Swim\tools\supabase_backup.bat"
```

The `.bat` wrapper writes `supabase-backups\last-run.log` and keeps the previous
run's log beside it.

## What a snapshot contains

```
supabase-backups\
  2026-09-12\
    manifest.json              row counts, byte sizes, warnings, failures
    _schema_openapi.json.gz    table + column listing as PostgREST sees it
    <table>.json.gz            one gzipped JSON array per table
  storage\                     bucket mirror, shared across snapshots
  last-run.log
```

Tables are **discovered** from PostgREST, not hardcoded, so a new table is picked up
without anyone remembering to add it. Storage is mirrored in place rather than per
date: the photos rarely change and copying ~48 MB nightly would be waste.

Every table is read back out of its own gzip and the row count re-counted before the
run is called a success, and the haul is compared against the server's own exact
count so a paging bug cannot silently truncate a table.

## Alerts

Failures push to **ntfy topic `FBscrapefailed`** — the same topic and phone app your
scrapers already use. A marker file (`backup_fail.marker`) means one push per failure
streak rather than one a night; it clears on the next good run.

It also warns, not just on hard failure, but when a table **shrinks by more than half**
or disappears versus the previous snapshot. A backup that quietly starts capturing
nothing is the dangerous failure, not the noisy one.

## Restoring

Snapshots hold **data, not schema**. PostgREST cannot export RLS policies, functions,
triggers or indexes, and `migrations/` is known to run behind the live database — so
do not assume it is a faithful schema record. Before relying on this for a full
rebuild, take a one-off schema dump (`supabase db dump --schema-only`, or the
dashboard's schema export) and keep it beside the snapshots.

To restore one table into an existing schema, POST the rows back in batches:

```python
import gzip, json, urllib.request
KEY  = open(r"C:\Users\stica\.viz-secrets\supabase_service_key.txt").read().strip()
URL  = "https://gkspukabnfbzrvjoewpc.supabase.co/rest/v1/<table>"
rows = json.load(gzip.open(r"<snapshot>\<table>.json.gz", "rt", encoding="utf-8"))
for i in range(0, len(rows), 500):
    req = urllib.request.Request(URL, data=json.dumps(rows[i:i+500]).encode(), method="POST")
    for h, v in (("apikey", KEY), ("Authorization", "Bearer " + KEY),
                 ("Content-Type", "application/json"), ("Prefer", "return=minimal")):
        req.add_header(h, v)
    urllib.request.urlopen(req).read()
```

Restore into an empty table. Re-running this against a populated one will collide on
primary keys.

## Retention

The last 30 dated snapshots are kept (`--keep N` to change). Pruning only ever removes
folders that match the date pattern **and** contain a `manifest.json`, so anything else
left in that directory is never touched.
