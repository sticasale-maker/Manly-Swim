#!/usr/bin/env python3
"""
supabase_backup.py - daily snapshot of the Manly Swim Supabase project.

This is the safety net that replaces the daily backups you lose on the Free
plan. Each run writes one gzipped JSON file per table into a dated folder in
Google Drive, mirrors the storage buckets incrementally, keeps a rolling
window of snapshots, and pushes an ntfy alert if a run fails or a table
shrinks unexpectedly.

THE KEY IS NEVER STORED IN THIS FILE OR IN THE REPO. Put the service_role key
in a local file, outside Google Drive so it cannot sync to the cloud:

    C:\\Users\\stica\\.viz-secrets\\supabase_service_key.txt

or set the SUPABASE_SERVICE_KEY environment variable instead.

The service_role key is required, not optional: the public key cannot read
bag_theft_reports or calibration_captures, and a backup that silently skips
your evidence tables is worse than no backup. The run aborts if the key it
finds cannot read them.

Usage:
    python supabase_backup.py                 # normal daily run
    python supabase_backup.py --allow-partial # smoke test with a weak key
    python supabase_backup.py --keep 60       # keep 60 snapshots instead of 30
    python supabase_backup.py --no-storage    # database only, skip the buckets
"""

import argparse
import datetime
import gzip
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROJECT_URL = "https://gkspukabnfbzrvjoewpc.supabase.co"
BACKUP_ROOT = r"C:\Users\stica\Google Drive\VIZ\APPS\Manly swim\supabase-backups"
KEY_FILE = r"C:\Users\stica\.viz-secrets\supabase_service_key.txt"
NTFY_TOPIC = "FBscrapefailed"          # same topic your scrapers already push to
FAIL_MARKER = "backup_fail.marker"     # push once per failure streak, like viz_fb_weekly.bat

# Tables the public key cannot read. If the configured key cannot read these,
# it is not the service_role key and the backup would be silently incomplete.
PROTECTED_TABLES = ["bag_theft_reports", "calibration_captures"]

# Only used if table discovery fails (it needs the service_role key too).
FALLBACK_TABLES = [
    "announcements", "bag_theft_reports", "bluebottle_reports",
    "calibration_captures", "feature_requests", "forecast_history", "intros",
    "site_sightings", "time_picker_poll", "visit_counter", "water_temp_now",
    "wind_obs_pairs",
]

PAGE = 1000
DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --------------------------------------------------------------------------- io

def load_key():
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if key:
        return key, "environment"
    if os.path.exists(KEY_FILE):
        key = open(KEY_FILE, encoding="utf-8").read().strip()
        if key:
            return key, KEY_FILE
    sys.exit(
        "No Supabase key found.\n"
        "  Create %s containing the service_role key\n"
        "  (Supabase dashboard - Project Settings - API Keys - service_role),\n"
        "  or set SUPABASE_SERVICE_KEY. Keep it out of Google Drive and the repo."
        % KEY_FILE
    )


def request(url, key, method="GET", body=None, headers=None, timeout=180, retries=3):
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("apikey", key)
        req.add_header("Authorization", "Bearer " + key)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                parsed = json.loads(raw) if raw else None
                return parsed, resp.headers
        except urllib.error.HTTPError as exc:
            # 4xx is a real answer (permissions, missing table) - do not retry.
            if exc.code < 500 or attempt == retries - 1:
                raise
        except Exception:
            if attempt == retries - 1:
                raise
        time.sleep(3 * (attempt + 1))


NOTIFY_ENABLED = True


def notify(title, message, tags="warning,floppy_disk", priority="high"):
    """Phone push, same channel as the scrapers. Never fatal."""
    if not NOTIFY_ENABLED:
        print("  (push suppressed) %s: %s" % (title, message[:80]))
        return
    try:
        req = urllib.request.Request(
            "https://ntfy.sh/" + NTFY_TOPIC,
            data=message.encode("utf-8"), method="POST")
        req.add_header("Title", title)
        req.add_header("Priority", priority)
        req.add_header("Tags", tags)
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as exc:
        print("  ! ntfy push failed: %s" % exc)


# ---------------------------------------------------------------- the database

def discover_tables(key):
    """PostgREST publishes its own table list, so new tables get backed up
    automatically instead of waiting for someone to update a hardcoded list."""
    try:
        spec, _ = request(PROJECT_URL + "/rest/v1/", key,
                          headers={"Accept": "application/openapi+json"})
        paths = (spec or {}).get("paths", {})
        tables = sorted(
            p.lstrip("/") for p in paths
            if p not in ("/",) and not p.startswith("/rpc/")
        )
        if tables:
            return tables, "discovered", spec
    except Exception as exc:
        print("  ! table discovery failed (%s); using the fallback list" % exc)
    return sorted(FALLBACK_TABLES), "fallback", None


def can_read(table, key):
    try:
        request("%s/rest/v1/%s?select=*&limit=1" % (PROJECT_URL, table), key)
        return True, ""
    except urllib.error.HTTPError as exc:
        return False, "HTTP %d" % exc.code
    except Exception as exc:
        return False, str(exc)[:60]


def order_column(table, key):
    """Offset paging needs a stable sort or rows can repeat or vanish."""
    try:
        rows, _ = request("%s/rest/v1/%s?select=*&limit=1" % (PROJECT_URL, table), key)
        if rows:
            cols = list(rows[0].keys())
            for preferred in ("id", "item_id", "user_id", "note_id", "reply_id"):
                if preferred in cols:
                    return preferred
            return cols[0]
    except Exception:
        pass
    return None


def exact_count(table, key):
    try:
        _, headers = request("%s/rest/v1/%s?select=*&limit=1" % (PROJECT_URL, table), key,
                             headers={"Prefer": "count=exact", "Range": "0-0"})
        tail = (headers.get("Content-Range") or "").split("/")[-1]
        return int(tail) if tail.isdigit() else None
    except Exception:
        return None


def dump_table(table, key, out_dir):
    col = order_column(table, key)
    expected = exact_count(table, key)
    order = "&order=%s.asc" % col if col else ""
    rows, offset = [], 0
    while True:
        url = "%s/rest/v1/%s?select=*%s&limit=%d&offset=%d" % (
            PROJECT_URL, table, order, PAGE, offset)
        chunk, _ = request(url, key)
        chunk = chunk or []
        rows.extend(chunk)
        offset += len(chunk)
        if len(chunk) < PAGE:
            break
    path = os.path.join(out_dir, table + ".json.gz")
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False)
    # Read it back: a backup nobody has opened is not yet a backup.
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        verified = len(json.load(fh))
    if verified != len(rows):
        raise RuntimeError("%s: wrote %d rows but read back %d" % (table, len(rows), verified))
    # Offset paging only returns everything if the sort is stable. Rather than
    # trust the column we guessed, check the haul against the server's own count.
    short = None
    if expected is not None and len(rows) < expected:
        short = expected - len(rows)
    return len(rows), os.path.getsize(path), col, short


# ----------------------------------------------------------------- the buckets

def list_objects(bucket, key, prefix=""):
    """Storage lists one folder level at a time, so recurse into the folders."""
    found, offset = [], 0
    while True:
        body = {"prefix": prefix, "limit": PAGE, "offset": offset,
                "sortBy": {"column": "name", "order": "asc"}}
        items, _ = request("%s/storage/v1/object/list/%s" % (PROJECT_URL, bucket),
                           key, method="POST", body=body)
        items = items or []
        for item in items:
            name = (prefix + "/" if prefix else "") + item["name"]
            if item.get("id") is None:          # a folder, not an object
                found.extend(list_objects(bucket, key, name))
            else:
                size = (item.get("metadata") or {}).get("size")
                found.append((name, size))
        offset += len(items)
        if len(items) < PAGE:
            return found


def mirror_storage(key, root, warnings):
    """Buckets are mirrored in place, not per-date: the photos rarely change and
    copying ~48 MB every night would be waste. New files are added, existing
    ones left alone; nothing here ever deletes."""
    store = os.path.join(root, "storage")
    os.makedirs(store, exist_ok=True)
    try:
        buckets, _ = request(PROJECT_URL + "/storage/v1/bucket", key)
    except Exception as exc:
        print("  ! could not list buckets: %s" % exc)
        warnings.append("storage: could not list buckets (%s)" % str(exc)[:80])
        return {"error": str(exc)[:120]}
    if not buckets:
        # A weak key gets an empty list here rather than an error, which would
        # otherwise look like a clean run that backed up no photos at all.
        print("  ! no buckets returned - storage was NOT backed up")
        warnings.append("storage: the API returned no buckets, so no photos were backed up")
        return {"error": "no buckets returned"}
    summary = {}
    for bucket in buckets or []:
        name = bucket["id"]
        try:
            objects = list_objects(name, key)
        except Exception as exc:
            print("  ! %s: listing failed (%s)" % (name, exc))
            summary[name] = {"error": str(exc)[:120]}
            continue
        added = skipped = 0
        for obj_name, size in objects:
            dest = os.path.join(store, name, obj_name.replace("/", os.sep))
            if os.path.exists(dest) and (size is None or os.path.getsize(dest) == size):
                skipped += 1
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            url = "%s/storage/v1/object/%s/%s" % (
                PROJECT_URL, name, urllib.parse.quote(obj_name))
            req = urllib.request.Request(url)
            req.add_header("apikey", key)
            req.add_header("Authorization", "Bearer " + key)
            try:
                with urllib.request.urlopen(req, timeout=180) as resp, open(dest, "wb") as fh:
                    shutil.copyfileobj(resp, fh)
                added += 1
            except Exception as exc:
                print("  ! %s/%s: %s" % (name, obj_name, exc))
        summary[name] = {"objects": len(objects), "new": added, "already_held": skipped}
        print("  %-18s %4d objects  %3d new  %4d already held" % (name, len(objects), added, skipped))
    return summary


# ------------------------------------------------------------------- snapshots

def previous_manifest(root, today_dir):
    dirs = sorted(d for d in os.listdir(root)
                  if DATE_DIR.match(d) and d != today_dir
                  and os.path.exists(os.path.join(root, d, "manifest.json")))
    if not dirs:
        return None
    return json.load(open(os.path.join(root, dirs[-1], "manifest.json"), encoding="utf-8"))


def prune(root, keep):
    """Only ever removes our own dated snapshot folders, and only ones carrying
    a manifest - so a stray folder in here is never touched."""
    dirs = sorted(d for d in os.listdir(root)
                  if DATE_DIR.match(d)
                  and os.path.exists(os.path.join(root, d, "manifest.json")))
    removed = []
    for old in dirs[:-keep] if keep > 0 else []:
        shutil.rmtree(os.path.join(root, old), ignore_errors=True)
        removed.append(old)
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=30, help="dated snapshots to retain")
    ap.add_argument("--allow-partial", action="store_true",
                    help="continue even if protected tables are unreadable (smoke test only)")
    ap.add_argument("--no-storage", action="store_true", help="skip the bucket mirror")
    ap.add_argument("--out", default=BACKUP_ROOT)
    ap.add_argument("--no-notify", action="store_true", help="suppress the phone push (testing)")
    args = ap.parse_args()

    global NOTIFY_ENABLED
    NOTIFY_ENABLED = not args.no_notify

    started = datetime.datetime.now()
    today = started.strftime("%Y-%m-%d")
    root = args.out
    out_dir = os.path.join(root, today)
    os.makedirs(out_dir, exist_ok=True)

    key, source = load_key()
    print("Supabase backup %s  (key from %s)" % (started.strftime("%Y-%m-%d %H:%M:%S"), source))

    warnings, failures = [], []

    # The guard that makes this backup trustworthy.
    unreadable = []
    for table in PROTECTED_TABLES:
        ok, why = can_read(table, key)
        if not ok:
            unreadable.append("%s (%s)" % (table, why))
    if unreadable:
        message = ("This key cannot read: %s. That means it is not the service_role "
                   "key, and those tables would be missing from the backup."
                   % ", ".join(unreadable))
        if not args.allow_partial:
            notify("Manly Swim backup: wrong key", message)
            sys.exit("ABORTED. " + message)
        warnings.append("PARTIAL RUN - " + message)
        print("  ! " + message)

    tables, how, spec = discover_tables(key)
    print("  %d tables (%s)" % (len(tables), how))
    if spec:
        with gzip.open(os.path.join(out_dir, "_schema_openapi.json.gz"), "wt", encoding="utf-8") as fh:
            json.dump(spec, fh, ensure_ascii=False)

    results, total_rows, total_bytes = {}, 0, 0
    for table in tables:
        try:
            rows, size, col, short = dump_table(table, key, out_dir)
            results[table] = {"rows": rows, "bytes": size, "ordered_by": col}
            total_rows += rows
            total_bytes += size
            if short:
                results[table]["short_by"] = short
                warnings.append("%s: collected %d rows but the server counted %d"
                                % (table, rows, rows + short))
            print("  %-24s %7d rows  %8.1f KB%s"
                  % (table, rows, size / 1024.0, "  SHORT BY %d" % short if short else ""))
        except Exception as exc:
            results[table] = {"error": str(exc)[:200]}
            failures.append("%s: %s" % (table, str(exc)[:120]))
            print("  %-24s FAILED: %s" % (table, str(exc)[:90]))

    # A backup that quietly starts returning nothing is the dangerous failure.
    previous = previous_manifest(root, today)
    if previous:
        for table, was in previous.get("tables", {}).items():
            before = was.get("rows")
            now = results.get(table, {}).get("rows")
            if before is None:
                continue
            if table not in results:
                warnings.append("%s vanished since %s" % (table, previous.get("date")))
            elif now is None:
                warnings.append("%s failed this run but held %d rows on %s"
                                % (table, before, previous.get("date")))
            elif before >= 20 and now < before * 0.5:
                warnings.append("%s dropped from %d to %d rows" % (table, before, now))

    storage = {}
    if not args.no_storage:
        print("  storage buckets:")
        storage = mirror_storage(key, root, warnings)

    manifest = {
        "date": today,
        "started": started.isoformat(timespec="seconds"),
        "finished": datetime.datetime.now().isoformat(timespec="seconds"),
        "project_url": PROJECT_URL,
        "discovery": how,
        "partial": bool(args.allow_partial and warnings),
        "tables": results,
        "total_rows": total_rows,
        "total_bytes": total_bytes,
        "storage": storage,
        "warnings": warnings,
        "failures": failures,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, ensure_ascii=False)

    removed = prune(root, args.keep)
    if removed:
        print("  pruned %d old snapshot(s): %s" % (len(removed), ", ".join(removed)))

    print("  %d rows, %.1f KB across %d tables -> %s"
          % (total_rows, total_bytes / 1024.0, len(results), out_dir))

    marker = os.path.join(root, FAIL_MARKER)
    if failures or warnings:
        title = "Manly Swim backup: %s" % ("FAILED" if failures else "check it")
        body = "\n".join(["Snapshot %s" % today] + failures + warnings)
        if not os.path.exists(marker):          # push once per streak
            notify(title, body)
            open(marker, "w", encoding="utf-8").write(body)
        print("  ! " + title)
        for line in failures + warnings:
            print("    - " + line)
        return 1 if failures else 0
    if os.path.exists(marker):                  # streak over, clear it
        os.remove(marker)
    print("  OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
