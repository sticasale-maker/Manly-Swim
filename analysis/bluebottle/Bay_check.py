#!/usr/bin/env python3
"""Bay-specific bluebottle check for Cabbage Tree Bay (South Steyne SW corner -> Shelly).
Compares two ways of scoring prior-3h onshore wind:
  (A) generic open-beach cosine on an 82 deg normal   [what the current band uses]
  (B) the bay's real fetch-by-direction weight (CHOP_FETCH_M from index.html)
Does the fetch weighting separate BAY strandings better than the generic cosine?

Run on your box (uses obs_sydney.csv + the wx_cache the main run already built):
  python bay_check.py
Self-test (no files needed; plants an NNE-driven signal):
  python bay_check.py --selftest
"""
import pandas as pd, numpy as np, math, os, sys
from sklearn.metrics import roc_auc_score

# bay coast ring, index = bearing/10 (wind FROM), 0 = N  (pulled from index.html, 14 Jul 2026)
CHOP_FETCH_M = [1766,2766,5936,5890,5456,4445,3599,2572,1622,800,257,232,
                171,127,109,94,72,57,50,46,46,47,52,60,
                70,82,105,144,192,238,412,624,789,1022,1273,1530]
FMAX = float(max(CHOP_FETCH_M))
BAY_BOX  = dict(lat=(-33.804,-33.796), lng=(151.287,151.304))
BAY_ASPECT = 82.0
WINDOW_H, DEFAULT_HR = 3, 8
CACHE, STUDY_START = "wx_cache", "2021-01-01"
BG_PER_CASE, BG_EXCLUDE = 3, 2

def fetch_weight(wd):                     # 0..1 bay exposure to wind FROM wd
    d = ((np.asarray(wd) % 360) + 360) % 360
    i = np.floor(d/10).astype(int); f = (d - i*10)/10.0
    a = np.array(CHOP_FETCH_M)[i]; b = np.array(CHOP_FETCH_M)[(i+1) % 36]
    return (a + (b-a)*f)/FMAX

def cos_onshore(ws, wd, brg=BAY_ASPECT):
    return ws * np.maximum(0.0, np.cos(np.radians(wd - brg)))

def fetch_onshore(ws, wd):
    return ws * fetch_weight(wd)

def local_dt(r):
    t = r.get('time_observed_at')
    if isinstance(t, str) and t:
        try: return pd.Timestamp(t).tz_convert('Australia/Sydney')
        except Exception: pass
    d = r.get('observed_on')
    if isinstance(d, str) and d:
        return pd.Timestamp(d + f" {DEFAULT_HR:02d}:00").tz_localize('Australia/Sydney')
    return pd.NaT

def bootstrap_auc(y, x, n=2000, seed=0):
    rng = np.random.default_rng(seed); idx = np.arange(len(y)); out = []
    for _ in range(n):
        s = rng.choice(idx, len(idx), replace=True)
        if len(np.unique(y[s])) < 2: continue
        out.append(roc_auc_score(y[s], x[s]))
    return np.percentile(out, [5, 50, 95])

def score(samp, wind):
    w = wind.set_index("time").sort_index()
    rows = []
    for _, r in samp.iterrows():
        t = pd.Timestamp(r["ldt"]).tz_localize(None)
        win = w.loc[t - pd.Timedelta(hours=WINDOW_H): t]
        if not len(win): continue
        ws, wd = win["ws"].values, win["wd"].values
        rows.append((r["y"], cos_onshore(ws, wd).mean(), fetch_onshore(ws, wd).mean()))
    d = pd.DataFrame(rows, columns=["y", "cos82", "fetch"])
    return d

def report(d):
    y = d["y"].values
    print(f"  samples: {len(d)}  strand={int(y.sum())}  typical={int((y==0).sum())}\n")
    for col, lab in [("cos82", "(A) generic cosine @82 deg  "), ("fetch", "(B) bay fetch-by-direction ")]:
        lo, mid, hi = bootstrap_auc(y, d[col].values)
        print(f"  {lab}: AUC {mid:.3f}  (90% CI {lo:.3f}-{hi:.3f})")
    da = roc_auc_score(y, d["fetch"]) - roc_auc_score(y, d["cos82"])
    print(f"\n  fetch-weighting vs generic cosine: {da:+.3f} AUC (point estimate)")
    return d

def load_bay_wind():
    files = [f for f in os.listdir(CACHE) if f.startswith("wind_") and f.endswith(".csv")]
    if not files: sys.exit(f"no wind cache in {CACHE}/ - run bluebottle_phase1.py first")
    cy, cx = np.mean(BAY_BOX["lat"]), np.mean(BAY_BOX["lng"]); best = None
    for f in files:
        parts = f[:-4].split("_"); la, lo = float(parts[-2]), float(parts[-1])
        dkm = np.hypot((la-cy)*110.54, (lo-cx)*111.32*math.cos(math.radians(cy)))
        if best is None or dkm < best[0]: best = (dkm, f)
    print(f"  bay wind cell: {best[1]}  ({best[0]*1000:.0f} m from bay centre)")
    return pd.read_csv(os.path.join(CACHE, best[1]), parse_dates=["time"])

def build_samples(obs):
    obs = obs.copy()
    obs["ldt"] = obs.apply(local_dt, axis=1)
    obs = obs.dropna(subset=["ldt"]); obs["date"] = obs["ldt"].dt.date
    pres = obs.drop_duplicates("date")[["date","ldt"]].copy(); pres["y"] = 1
    end = pd.Timestamp.now(tz="Australia/Sydney").normalize()
    span = pd.date_range(pd.Timestamp(STUDY_START).tz_localize("Australia/Sydney"), end, freq="D")
    import datetime as dt
    excl = {d+dt.timedelta(days=k) for d in pres["date"] for k in range(-BG_EXCLUDE, BG_EXCLUDE+1)}
    pool = [d for d in span if d.date() not in excl]
    rng = np.random.default_rng(42)
    pick = rng.choice(len(pool), min(len(pres)*BG_PER_CASE, len(pool)), replace=False)
    bg = pd.DataFrame({"ldt":[pool[i].tz_convert("Australia/Sydney").replace(hour=DEFAULT_HR) for i in pick]})
    bg["y"] = 0
    return pd.concat([pres[["ldt","y"]], bg], ignore_index=True)

def selftest():
    print("SELFTEST: plant NNE-driven bay strandings; fetch weight (NNE-peaked) should beat cos@82")
    rng = np.random.default_rng(0)
    times = pd.date_range("2022-01-01","2024-12-31 23:00", freq="h"); days = times.normalize()
    uniq = pd.unique(days)
    dd = {d: rng.uniform(0,360) for d in uniq}; ds = {d: float(np.clip(rng.normal(18,7),3,None)) for d in uniq}
    wd = (np.array([dd[d] for d in days]) + rng.normal(0,12,len(times))) % 360
    ws = np.clip(np.array([ds[d] for d in days]) + rng.normal(0,3,len(times)), 0, None)
    wind = pd.DataFrame(dict(time=times, ws=ws, wd=wd))
    w = wind.set_index("time")
    rows=[]
    import datetime as dt
    for d in pd.date_range("2022-01-01","2024-12-31",freq="D"):
        t = pd.Timestamp(d)+pd.Timedelta(hours=DEFAULT_HR)
        win = w.loc[t-pd.Timedelta(hours=WINDOW_H):t]
        drive = (win["ws"].values*fetch_weight(win["wd"].values)).mean()   # true driver = fetch
        p = 1/(1+math.exp(-(drive-6)/2))
        if rng.random() < 0.15*p:
            rows.append(dict(ldt=t.tz_localize("Australia/Sydney"), y=1))
    pres=pd.DataFrame(rows)
    span=pd.date_range("2022-01-01","2024-12-31",freq="D")
    excl={d+dt.timedelta(days=k) for d in pres["ldt"].dt.date for k in range(-2,3)}
    pool=[d for d in span if d.date() not in excl]
    pick=rng.choice(len(pool),len(pres)*3,replace=False)
    bg=pd.DataFrame({"ldt":[(pool[i]+pd.Timedelta(hours=DEFAULT_HR)).tz_localize("Australia/Sydney") for i in pick]}); bg["y"]=0
    samp=pd.concat([pres,bg],ignore_index=True)
    d=report(score(samp, wind))
    a_cos=roc_auc_score(d["y"],d["cos82"]); a_f=roc_auc_score(d["y"],d["fetch"])
    assert a_f>a_cos, "selftest: fetch weight did not beat cosine on NNE-planted signal"
    print("\nSELFTEST PASSED - fetch weighting recovers the NNE-specific signal better than cos@82.")

def main():
    if "--selftest" in sys.argv: selftest(); return
    obs = pd.read_csv("obs_sydney.csv")
    bay = obs[(obs.lat.between(*BAY_BOX["lat"])) & (obs.lng.between(*BAY_BOX["lng"]))].copy()
    print(f"[bay] {len(bay)} iNat records inside Cabbage Tree Bay box")
    samp = build_samples(bay)
    wind = load_bay_wind()
    print("\nBAY-SPECIFIC 3h onshore — generic cosine vs your fetch geometry:")
    report(score(samp, wind))

if __name__ == "__main__":
    main()