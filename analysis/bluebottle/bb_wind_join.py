#!/usr/bin/env python3
"""
bb_wind_join.py  â€”  STEP 1, runs on YOUR PC (needs internet to Open-Meteo).

Purpose
-------
Turn obs_sydney.csv (presence-only iNaturalist sightings) into a matched
case-control dataset with the ERA5 wind attached, so the aspect refit can run
offline in a chat session afterwards. This is the ONE piece the Claude sandbox
can't do itself â€” it can't reach Open-Meteo. Everything downstream can.

What it emits
-------------
bb_windjoined.csv â€” one row per (beach, day) that is either a stranding-day
(is_case=1) or a matched control day (is_case=0). Each row carries a 30-hour
block of hourly wind (speed + from-direction) covering [day-1 18:00 .. day 23:00],
so the refit harness can recompute the onshore feature for ANY candidate aspect
and ANY trailing window â€” aspect and lag stay free parameters, nothing is baked in.

Run it
------
  pip install requests pandas
  python bb_wind_join.py            # or your full path:
  # C:\\Users\\stica\\AppData\\Local\\Python\\bin\\python.exe bb_wind_join.py
Put obs_sydney.csv next to this file first (from docs/data/ in the repo).
Wind is cached under wx_cache/ so re-runs are instant.

DESIGN DECISIONS are flagged inline with  # >>> DECISION  â€” these are the calls
the refined-analysis session should own, not accept blindly.
"""

import csv, json, math, os, time, random, sys
import requests
import pandas as pd

random.seed(42)                       # reproducible control sampling

# --- Beach gazetteer: name, lat, lng, shore_normal(deg, wind-FROM = onshore), region
# shore_normal is the geographic facing estimate. The refit will test whether these
# per-beach values beat the single 82 deg the app currently uses everywhere.
BEACHES = [
    ("Palm Beach",       -33.598, 151.326, 100, "north"),
    ("Whale Beach",      -33.613, 151.333,  95, "north"),
    ("Avalon",           -33.635, 151.330, 105, "north"),
    ("Bilgola",          -33.646, 151.326, 100, "north"),
    ("Newport",          -33.656, 151.323, 100, "north"),
    ("Mona Vale",        -33.679, 151.308, 105, "north"),
    ("Warriewood",       -33.693, 151.305, 110, "north"),
    ("Narrabeen",        -33.710, 151.299, 110, "north"),
    ("Collaroy",         -33.733, 151.301, 105, "north"),
    ("Dee Why",          -33.753, 151.298, 100, "north"),
    ("Curl Curl",        -33.771, 151.296,  95, "north"),
    ("Freshwater",       -33.781, 151.292,  90, "north"),
    ("Manly",            -33.796, 151.288,  85, "north"),
    ("Cabbage Tree Bay", -33.7998,151.290,  55, "north"),
    ("Shelly Beach",     -33.8018,151.2955, 20, "north"),
    ("Bondi",            -33.8915,151.2767,120, "east"),
    ("Tamarama",         -33.899, 151.271, 115, "east"),
    ("Bronte",           -33.903, 151.268, 110, "east"),
    ("Clovelly",         -33.914, 151.267,  90, "east"),
    ("Coogee",           -33.920, 151.258, 105, "east"),
    ("Maroubra",         -33.950, 151.257, 100, "east"),
    ("Malabar",          -33.968, 151.251,  70, "east"),
    ("Cronulla",         -34.057, 151.156, 110, "south"),
]

N_CONTROLS   = 3                      # >>> DECISION: 3 controls/case (matches the model doc)
SPAN_START   = "2006-01-01"          # >>> DECISION: modelled span. Doc used 2021-26 for the
SPAN_END     = None                  #     wind fit "where hourly available"; ERA5 covers 2011+.
MATCH_MONTH  = False                 # >>> DECISION: if True, controls drawn from same calendar
                                     #     month as their case (removes the seasonal wind signal â€”
                                     #     only do this if you want the WITHIN-season effect).
FEATURE_HRS  = 30                    # block length; supports trailing-3h at any hour incl. 00:00
CACHE_DIR    = "wx_cache"
ARCHIVE_URL  = "https://archive-api.open-meteo.com/v1/archive"


def haversine(a, b, c, d):
    R = 6371.0; p = math.pi / 180
    return 2*R*math.asin(math.sqrt(
        math.sin((c-a)*p/2)**2 + math.cos(a*p)*math.cos(c*p)*math.sin((d-b)*p/2)**2))


def assign_beach(lat, lng):
    best, bd = None, 1e9
    for i, (n, bla, blo, asp, reg) in enumerate(BEACHES):
        dd = haversine(lat, lng, bla, blo)
        if dd < bd:
            bd, best = dd, i
    return best if bd <= 4.0 else None      # >4 km from any beach = fuzzy "Sydney", drop


def fetch_beach_wind(name, lat, lng, end_date):
    """Hourly ERA5 wind for the whole span, cached. Returns DataFrame indexed by
    naive Sydney-local datetime with columns spd (km/h) and dir (deg, wind-FROM)."""
    safe = name.replace(" ", "_").replace("/", "_")
    path = os.path.join(CACHE_DIR, safe + ".csv")
    if os.path.exists(path):
        df = pd.read_csv(path, parse_dates=["time"])
        return df.set_index("time")
    params = {
        "latitude": lat, "longitude": lng,
        "start_date": SPAN_START, "end_date": end_date,
        "hourly": "wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "kmh",
        "timezone": "Australia/Sydney",
        "models": "era5",                    # match the model doc's ERA5 reanalysis
    }
    for attempt in range(4):
        try:
            r = requests.get(ARCHIVE_URL, params=params, timeout=120)
            r.raise_for_status()
            h = r.json()["hourly"]
            df = pd.DataFrame({
                "time": pd.to_datetime(h["time"]),
                "spd":  h["wind_speed_10m"],
                "dir":  h["wind_direction_10m"],
            })
            os.makedirs(CACHE_DIR, exist_ok=True)
            df.to_csv(path, index=False)
            time.sleep(1.0)                  # be polite between beaches
            return df.set_index("time")
        except Exception as e:
            print(f"  retry {attempt+1} for {name}: {e}", file=sys.stderr)
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"could not fetch wind for {name}")


def block_for(series, day):
    """30 hourly (spd,dir) samples: [day-1 18:00 .. day 23:00]. NaN-padded at span edges."""
    start = pd.Timestamp(day) - pd.Timedelta(hours=6)      # day-1 18:00
    idx = [start + pd.Timedelta(hours=h) for h in range(FEATURE_HRS)]
    sub = series.reindex(idx)
    spd = [None if pd.isna(v) else round(float(v), 1) for v in sub["spd"]]
    dr  = [None if pd.isna(v) else round(float(v), 1) for v in sub["dir"]]
    return spd, dr


def main():
    if not os.path.exists("obs_sydney.csv"):
        sys.exit("Put obs_sydney.csv next to this script (from repo docs/data/).")

    end_date = SPAN_END or pd.Timestamp.today().strftime("%Y-%m-%d")

    # 1) read sightings, assign to beaches, collect stranding-days per beach
    case_days = {}          # beach_idx -> {date_str -> first obs_hour (or None)}
    with open("obs_sydney.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                lat, lng = float(row["lat"]), float(row["lng"])
            except (TypeError, ValueError):
                continue
            bi = assign_beach(lat, lng)
            if bi is None:
                continue
            d = (row.get("observed_on") or "")[:10]
            if len(d) != 10:
                continue                       # 6 date-less records drop out (as in the doc)
            t = row.get("time_observed_at") or ""
            hr = int(t[11:13]) if len(t) >= 13 and t[11:13].isdigit() else None
            case_days.setdefault(bi, {})
            if d not in case_days[bi]:          # collapse same-day/same-beach â†’ one case
                case_days[bi][d] = hr

    # 2) per beach: fetch wind, emit cases + matched controls
    out_rows = []
    for bi, (name, lat, lng, asp, reg) in enumerate(BEACHES):
        if bi not in case_days or not case_days[bi]:
            continue
        print(f"[{name}] {len(case_days[bi])} stranding-days â€” fetching windâ€¦")
        series = fetch_beach_wind(name, lat, lng, end_date)
        span_days = pd.date_range(SPAN_START, end_date, freq="D")
        case_set = set(case_days[bi].keys())

        for case_id, (d, hr) in enumerate(sorted(case_days[bi].items())):
            day = pd.Timestamp(d)
            spd, dr = block_for(series, day)
            gid = f"{bi}-{case_id}"
            out_rows.append(dict(case_id=gid, beach=name, region=reg, shore_normal=asp,
                                 date=d, is_case=1, obs_hour=hr, month=day.month, year=day.year,
                                 blk_spd=json.dumps(spd), blk_dir=json.dumps(dr)))
            # matched controls
            if MATCH_MONTH:
                pool = [dd for dd in span_days if dd.month == day.month
                        and dd.strftime("%Y-%m-%d") not in case_set]
            else:
                pool = [dd for dd in span_days if dd.strftime("%Y-%m-%d") not in case_set]
            picks = random.sample(pool, min(N_CONTROLS, len(pool)))
            for cd in picks:
                cspd, cdr = block_for(series, cd)
                out_rows.append(dict(case_id=gid, beach=name, region=reg, shore_normal=asp,
                                     date=cd.strftime("%Y-%m-%d"), is_case=0,
                                     obs_hour=hr,               # matched hour-of-day to its case
                                     month=cd.month, year=cd.year,
                                     blk_spd=json.dumps(cspd), blk_dir=json.dumps(cdr)))

    out = pd.DataFrame(out_rows)
    out.to_csv("bb_windjoined.csv", index=False)
    n_case = int(out.is_case.sum()); n_ctrl = len(out) - n_case
    print(f"\nWrote bb_windjoined.csv â€” {n_case} cases, {n_ctrl} controls, "
          f"{out.beach.nunique()} beaches.")
    print("Upload that file into the Fable session with the brief. Nothing here "
          "touches the app or the sheet.")


if __name__ == "__main__":
    main()



