#!/usr/bin/env python3
"""
Bluebottle Phase-1 signal check — wider Sydney region.
Does onshore wind (lagged) predict a stranding DAY at a given beach?

Design (honest, presence-only aware):
  * iNaturalist Physalia (genus) in a Sydney box = PRESENCE only. We reframe as a matched
    case/control: presence-day vs typical-day AT THE SAME beach cluster.
    That controls for "where people look" (spatial effort) and isolates
    the temporal weather signal. It yields RELATIVE risk, not an absolute
    calibrated probability (that needs 'none-seen' data from the app).
  * Weather from Open-Meteo ERA5 archive (wind, 1940+; solid history).
    Marine (waves/swell) attached where the archive has it; secondary.
  * Feature = onshore wind component over lag windows ending at the check
    time. onshore = ws * max(0, cos(wind_from_dir - offshore_bearing)).
    offshore_bearing defaults 90 deg (Sydney ocean beaches face ~E).

Stages: pull obs -> presence-days -> matched background -> fetch weather
        (cached per grid cell) -> lag features -> blocked-CV models -> report.

Run on your Windows box:
  python bluebottle_phase1.py
Self-test (no network, planted signal):
  python bluebottle_phase1.py --selftest
"""
import os, sys, time, json, math, urllib.request, urllib.parse, datetime as dt
import numpy as np, pandas as pd
import ssl
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl.create_default_context()

# ---- config -----------------------------------------------------------------
PROJECT_ID   = 115085          # legacy (manual project); no longer queried
TAXON_NAME   = "Physalia"      # genus; descendants (all bluebottle species) included
SYD_BOX      = dict(swlat=-34.15, swlng=151.05, nelat=-33.45, nelng=151.35)  # Cronulla->Palm Bch
STUDY_START  = "2021-01-01"
OFFSHORE_BRG = 90.0          # deg; DEFAULT offshore bearing (east) when no per-beach aspect
# Per-beach offshore-bearing overrides near authoritative anchors (name, lat, lng, bearing).
# Geometric aspect misfires at harbour/bay mouths, so we pin the beaches we KNOW:
# Maroubra/Coogee/Clovelly from Bourg et al. 2022; Manly = Marco's call (TUNE this one).
ASPECT_OVERRIDES = [
    ("Maroubra",     -33.950, 151.257,  90),
    ("Coogee",       -33.921, 151.257, 115),
    ("Clovelly",     -33.914, 151.267, 155),
    ("Manly/STeyne", -33.797, 151.288,  82),
    ("PalmBeach",    -33.600, 151.325,  80),
]
OVR_R_KM = 1.2
LAG_WINDOWS  = [1, 2, 3, 6, 9, 12, 24, 48]
CLUSTER_DP   = 2             # obs cluster rounding (~1.1 km) -> "same beach"
WX_DP        = 2             # weather grid rounding (~1 km) -> per-beach wind, no aliasing
WIND_SOURCE  = "hires"       # "hires" = Open-Meteo Historical Forecast (~2-11 km); "era5" = archive (~11-25 km)
WIND_ENDPOINT = {"hires": "https://historical-forecast-api.open-meteo.com/v1/forecast",
                 "era5":  "https://archive-api.open-meteo.com/v1/archive"}
BG_PER_CASE  = 3             # background (control) days per presence day
BG_EXCLUDE   = 2             # exclude control dates within +-N days of any presence@cluster
DEFAULT_HR   = 8             # assumed local hour when obs has date only (morning check)
CACHE        = "wx_cache"
UA           = "SwimManly-VIZ/1.0 (bluebottle study; sticasale@gmail.com)"

# ---- iNaturalist ------------------------------------------------------------
def resolve_taxon(name):
    d = _get("https://api.inaturalist.org/v1/taxa?" +
             urllib.parse.urlencode(dict(q=name, rank="genus", per_page=5)))
    for t in d.get("results", []):
        if t.get("name", "").lower() == name.lower() and t.get("rank") == "genus":
            print(f"  taxon: {t['name']} (genus) id={t['id']}"); return t["id"]
    res = d.get("results") or []
    if res: return res[0]["id"]
    raise SystemExit(f"could not resolve taxon {name!r}")

def pull_obs():
    base = "https://api.inaturalist.org/v1/observations"
    taxon_id = resolve_taxon(TAXON_NAME)
    rows, id_above, total = [], 0, None
    while True:
        q = dict(taxon_id=taxon_id, per_page=200, order_by="id", order="asc",
                 id_above=id_above,
                 swlat=SYD_BOX["swlat"], swlng=SYD_BOX["swlng"],
                 nelat=SYD_BOX["nelat"], nelng=SYD_BOX["nelng"])
        url = base + "?" + urllib.parse.urlencode(q)
        d = _get(url)
        if total is None:
            total = d.get("total_results"); print(f"  iNat total in box: {total}")
        res = d.get("results") or []
        if not res: break
        for o in res:
            loc = o.get("location")
            if not loc or "," not in loc: continue
            lat, lng = [float(x) for x in loc.split(",", 1)]
            rows.append(dict(id=o["id"], lat=lat, lng=lng,
                             geoprivacy=o.get("geoprivacy") or "open",
                             observed_on=o.get("observed_on"),
                             time_observed_at=o.get("time_observed_at"),
                             place_guess=o.get("place_guess") or ""))
        id_above = res[-1]["id"]
        print(f"  fetched {len(rows)}/{total}"); time.sleep(1.0)
    df = pd.DataFrame(rows)
    n0 = len(df)
    df = df[df["geoprivacy"] != "obscured"].copy()   # drop ~20km-fuzzed points
    print(f"  kept {len(df)}/{n0} after dropping obscured")
    return df

def local_dt(row):
    """Best local timestamp for the check."""
    t = row.get("time_observed_at")
    if isinstance(t, str) and t:
        try:
            return pd.Timestamp(t).tz_convert("Australia/Sydney")
        except Exception:
            pass
    d = row.get("observed_on")
    if isinstance(d, str) and d:
        return pd.Timestamp(d + f" {DEFAULT_HR:02d}:00").tz_localize("Australia/Sydney")
    return pd.NaT

# ---- presence-days + matched background -------------------------------------
def make_samples(obs):
    obs = obs.copy()
    obs["cluster"] = list(zip(obs["lat"].round(CLUSTER_DP), obs["lng"].round(CLUSTER_DP)))
    obs["ldt"] = obs.apply(local_dt, axis=1)
    obs = obs.dropna(subset=["ldt"])
    obs["date"] = obs["ldt"].dt.date
    # collapse to one presence-day per (cluster, date)
    pres = (obs.sort_values("ldt")
              .groupby(["cluster", "date"], as_index=False)
              .agg(lat=("lat", "mean"), lng=("lng", "mean"), ldt=("ldt", "first")))
    pres["y"] = 1
    end = pd.Timestamp.now(tz="Australia/Sydney").normalize()
    start = pd.Timestamp(STUDY_START).tz_localize("Australia/Sydney")
    span = pd.date_range(start, end, freq="D")
    rng = np.random.default_rng(42)
    bg = []
    for cl, g in pres.groupby("cluster"):
        pdset = set(g["date"])
        excl = set()
        for d0 in pdset:
            for k in range(-BG_EXCLUDE, BG_EXCLUDE + 1):
                excl.add(d0 + dt.timedelta(days=k))
        pool = [d for d in span if d.date() not in excl]
        need = len(g) * BG_PER_CASE
        if not pool: continue
        pick = rng.choice(len(pool), size=min(need, len(pool)), replace=False)
        lat, lng = g["lat"].mean(), g["lng"].mean()
        for i in pick:
            d = pool[i]
            bg.append(dict(cluster=cl, date=d.date(), lat=lat, lng=lng,
                           ldt=d.tz_convert("Australia/Sydney").replace(hour=DEFAULT_HR),
                           y=0))
    samp = pd.concat([pres, pd.DataFrame(bg)], ignore_index=True)
    print(f"  presence-days={int((samp.y==1).sum())}  background-days={int((samp.y==0).sum())}"
          f"  clusters={samp.cluster.nunique()}")
    return samp

# ---- Open-Meteo weather (cached per grid cell) ------------------------------
def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90, context=_SSL_CTX) as r:
                return json.load(r)
        except Exception as e:
            if attempt == 3: raise
            time.sleep(2 * (attempt + 1))

def fetch_cell_wind(lat, lng, start, end, source):
    q = dict(latitude=round(lat, WX_DP), longitude=round(lng, WX_DP),
             start_date=start, end_date=end,
             hourly="wind_speed_10m,wind_direction_10m",
             wind_speed_unit="kmh", timezone="Australia/Sydney")
    url = WIND_ENDPOINT[source] + "?" + urllib.parse.urlencode(q)
    d = _get(url)["hourly"]
    return pd.DataFrame(dict(time=pd.to_datetime(d["time"]),
                             ws=d["wind_speed_10m"], wd=d["wind_direction_10m"]))

def build_weather(samp):
    os.makedirs(CACHE, exist_ok=True)
    start = STUDY_START
    end = pd.Timestamp.now(tz="Australia/Sydney").strftime("%Y-%m-%d")
    keys = sorted(set(zip(samp["lat"].round(WX_DP), samp["lng"].round(WX_DP))))
    store = {}
    for n, (la, lo) in enumerate(keys, 1):
        fp = os.path.join(CACHE, f"wind_{WIND_SOURCE}_{la}_{lo}.csv")
        if os.path.exists(fp):
            store[(la, lo)] = pd.read_csv(fp, parse_dates=["time"]); continue
        try:
            w = fetch_cell_wind(la, lo, start, end, WIND_SOURCE)
            src = WIND_SOURCE
        except Exception as e:                         # hi-res gap -> fall back to ERA5
            print(f"  [{n}/{len(keys)}] {la},{lo} {WIND_SOURCE} failed ({e}); ERA5 fallback")
            w = fetch_cell_wind(la, lo, start, end, "era5"); src = "era5"
        # drop rows with no wind (pre-coverage hours in hi-res archive)
        w = w.dropna(subset=["ws", "wd"])
        w.to_csv(fp, index=False); store[(la, lo)] = w
        print(f"  [{n}/{len(keys)}] wind cell {la},{lo} <- {src}  ({len(w)} hrs)")
        time.sleep(1.0)
    return store

# ---- features ---------------------------------------------------------------
def onshore_series(ws, wd, brg):
    return ws * np.maximum(0.0, np.cos(np.radians(wd - brg)))

def onshore(ws, wd, brg=OFFSHORE_BRG):      # backward-compat shim (selftest synthetic gen)
    return onshore_series(ws, wd, brg)

def build_aspect(samp):
    """Per-beach offshore bearing (deg). Geometric coastline-normal from the spatial
    trace of ALL clusters (position only, not stranding outcome), overridden near
    authoritative anchors where geometry is unreliable (harbour/bay mouths)."""
    cls = sorted(set(samp["cluster"]))
    lat0 = np.mean([c[0] for c in cls])
    X = np.array([[c[1]*np.cos(np.radians(lat0))*111320.0, c[0]*110540.0] for c in cls])
    amap = {}
    for i, c in enumerate(cls):
        best = None
        for nm, ala, alo, ab in ASPECT_OVERRIDES:
            dkm = np.hypot((c[0]-ala)*110.54, (c[1]-alo)*111.32*np.cos(np.radians(c[0])))
            if dkm <= OVR_R_KM and (best is None or dkm < best[0]): best = (dkm, ab)
        if best: amap[c] = float(best[1]); continue
        d = np.hypot(X[:,0]-X[i,0], X[:,1]-X[i,1]); sel = d <= 2500.0
        if sel.sum() < 4:
            o = np.argsort(d); sel = np.zeros(len(d), bool); sel[o[:min(5,len(d))]] = True
        P = X[sel] - X[sel].mean(0)
        if len(P) < 2 or np.allclose(P, 0.0): amap[c] = OFFSHORE_BRG; continue
        w, V = np.linalg.eigh(np.cov(P.T)); t = V[:, int(np.argmax(w))]
        n = np.array([t[1], -t[0]]); n = -n if n[0] < 0 else n   # seaward = eastward
        amap[c] = float((np.degrees(np.arctan2(n[0], n[1])) + 360) % 360)
    return amap

def add_features(samp, store, aspect_map=None):
    feats = []
    idx = {k: v.set_index("time").sort_index() for k, v in store.items()}   # raw ws, wd
    for _, r in samp.iterrows():
        key = (round(r["lat"], WX_DP), round(r["lng"], WX_DP))
        s = idx.get(key)
        brg = OFFSHORE_BRG if aspect_map is None else float(aspect_map.get(r["cluster"], OFFSHORE_BRG))
        row = {"y": r["y"], "cluster": str(r["cluster"]), "aspect": brg,
               "year": pd.Timestamp(r["ldt"]).year, "doy": pd.Timestamp(r["ldt"]).dayofyear}
        t = pd.Timestamp(r["ldt"]).tz_localize(None)
        ok = s is not None
        for W in LAG_WINDOWS:
            win = s.loc[t - pd.Timedelta(hours=W): t] if ok else None
            if win is not None and len(win):
                wsv = win["ws"].values; wdv = win["wd"].values
                onsh   = onshore_series(wsv, wdv, brg)             # per-beach aspect
                onsh90 = onshore_series(wsv, wdv, OFFSHORE_BRG)    # flat-90 reference
                row[f"onsh_mean_{W}"]   = float(np.mean(onsh))
                row[f"onsh_max_{W}"]    = float(np.max(onsh))
                row[f"onsh90_mean_{W}"] = float(np.mean(onsh90))
                row[f"ws_mean_{W}"]     = float(np.mean(wsv))
            else:
                for k in (f"onsh_mean_{W}", f"onsh_max_{W}", f"onsh90_mean_{W}", f"ws_mean_{W}"):
                    row[k] = np.nan
        row["sin_doy"] = math.sin(2*math.pi*row["doy"]/365.25)
        row["cos_doy"] = math.cos(2*math.pi*row["doy"]/365.25)
        feats.append(row)
    F = pd.DataFrame(feats).dropna()
    print(f"  feature rows: {len(F)}  (pos={int(F.y.sum())})")
    return F

# ---- models + blocked CV ----------------------------------------------------
def evaluate(F):
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import GroupKFold
    from sklearn.metrics import roc_auc_score

    onsh_cols   = [c for c in F.columns if c.startswith("onsh_")]      # per-beach aspect
    onsh90_cols = [c for c in F.columns if c.startswith("onsh90_")]    # flat-90 reference
    ws_cols     = [c for c in F.columns if c.startswith("ws_")]
    wind_cols   = onsh_cols + ws_cols
    wind_flat   = onsh90_cols + ws_cols
    seas_cols   = ["sin_doy", "cos_doy"]
    y = F["y"].values
    groups = F["year"].values                     # blocked by year (no temporal leakage)
    n_splits = min(5, len(np.unique(groups)))
    gkf = GroupKFold(n_splits=max(2, n_splits))

    def cv_auc(cols):
        aucs = []
        for tr, te in gkf.split(F, y, groups):
            sc = StandardScaler().fit(F.iloc[tr][cols])
            Xtr, Xte = sc.transform(F.iloc[tr][cols]), sc.transform(F.iloc[te][cols])
            m = LogisticRegression(max_iter=1000).fit(Xtr, y[tr])
            if len(np.unique(y[te])) < 2: continue
            aucs.append(roc_auc_score(y[te], m.predict_proba(Xte)[:, 1]))
        return np.array(aucs)

    auc_season = cv_auc(seas_cols)
    auc_flat   = cv_auc(wind_flat)     # single east normal (old model)
    auc_wind   = cv_auc(wind_cols)     # per-beach aspect (new)
    auc_both   = cv_auc(wind_cols + seas_cols)
    sweep = {W: cv_auc([f"onsh_mean_{W}"]) for W in LAG_WINDOWS}   # single-feature, blocked CV
    sweep_mu = {W: (a.mean() if len(a) else float("nan")) for W, a in sweep.items()}
    best_W = max(sweep_mu, key=lambda k: (sweep_mu[k] if sweep_mu[k] == sweep_mu[k] else -1))

    # odds ratios from full-data logistic (standardised) for interpretation
    sc = StandardScaler().fit(F[wind_cols + seas_cols])
    lr = LogisticRegression(max_iter=1000).fit(sc.transform(F[wind_cols + seas_cols]), y)
    ors = pd.Series(np.exp(lr.coef_[0]), index=wind_cols + seas_cols).sort_values(ascending=False)

    # nonlinear check
    gb_auc = []
    for tr, te in gkf.split(F, y, groups):
        gb = GradientBoostingClassifier().fit(F.iloc[tr][wind_cols + seas_cols], y[tr])
        if len(np.unique(y[te])) < 2: continue
        gb_auc.append(roc_auc_score(y[te], gb.predict_proba(F.iloc[te][wind_cols + seas_cols])[:, 1]))
    gb_auc = np.array(gb_auc)

    def fmt(a): return f"{a.mean():.3f} ± {a.std():.3f} (n={len(a)})" if len(a) else "n/a"
    lines = [
        "="*64, "BLUEBOTTLE PHASE-1 SIGNAL CHECK — RESULTS", "="*64,
        f"samples: {len(F)}  presence={int(y.sum())}  background={int((y==0).sum())}",
        f"CV: GroupKFold blocked by year ({gkf.get_n_splits()} folds)", "",
        "AUC (higher = stronger signal; 0.5 = none):",
        f"  season only             : {fmt(auc_season)}",
        f"  wind, flat 90 normal    : {fmt(auc_flat)}",
        f"  wind, per-beach aspect  : {fmt(auc_wind)}",
        f"  aspect wind + season    : {fmt(auc_both)}",
        f"  gradient boost          : {fmt(gb_auc)}", "",
        (f"Per-beach aspect vs flat 90 :  {(auc_wind.mean()-auc_flat.mean()):+.3f} AUC"
         if len(auc_wind) and len(auc_flat) else "aspect vs flat: n/a"),
        (f"Wind (aspect) over season   :  {(auc_both.mean()-auc_season.mean()):+.3f} AUC"
         if len(auc_both) and len(auc_season) else "n/a"),
        "",
        "Lag-window sweep - single-feature AUC of prior-Nh onshore wind:",
        *[f"  {W:>3d}h : {fmt(sweep[W])}" + ("   <- sharpest" if W == best_W else "")
          for W in LAG_WINDOWS],
        f"  => use prior-{best_W}h onshore wind as the band driver",
        "",
        "Standardised odds ratios (>1 raises stranding odds):",
    ]
    for k, v in ors.items():
        lines.append(f"  {k:14s} {v:5.2f}")
    verdict = ("SIGNAL PRESENT — onshore wind separates stranding days."
               if len(auc_wind) and auc_wind.mean() >= 0.60
               else "WEAK/NO SIGNAL at this resolution — revisit features/aspect.")
    lines += ["", "VERDICT: " + verdict, "="*64]
    report = "\n".join(lines)
    return report, dict(auc_wind=auc_wind, auc_flat=auc_flat, auc_season=auc_season, auc_both=auc_both, ors=ors, F=F, best_W=best_W)

def plots(res):
    try:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        F = res["F"]
        fig, ax = plt.subplots(1, 2, figsize=(11, 4))
        for lab, sub in [("stranding", F[F.y==1]), ("typical", F[F.y==0])]:
            ax[0].hist(sub["onsh_mean_24"], bins=30, alpha=0.5, density=True, label=lab)
        ax[0].set_title("Onshore wind (24h mean) — stranding vs typical")
        ax[0].set_xlabel("onshore component (km/h)"); ax[0].legend()
        res["ors"].plot.barh(ax=ax[1]); ax[1].axvline(1, color="k", lw=.8)
        ax[1].set_title("Odds ratios (standardised)")
        fig.tight_layout(); fig.savefig("phase1_plot.png", dpi=130)
        print("  wrote phase1_plot.png")
    except Exception as e:
        print("  (plot skipped:", e, ")")

# ---- self-test (no network) -------------------------------------------------
def selftest():
    print("SELFTEST: synthetic weather with planted onshore-wind -> stranding signal")
    rng = np.random.default_rng(0)
    cells = [(-33.8, 151.3), (-33.9, 151.3), (-33.6, 151.3)]
    times = pd.date_range("2022-01-01", "2024-12-31 23:00", freq="h")
    days = times.normalize(); uniq = pd.unique(days)
    store = {}
    for (la, lo) in cells:
        # each DAY gets its own synoptic regime (prevailing dir + speed); hourly = day + diurnal + noise
        dd = {d: rng.uniform(0, 360) for d in uniq}
        ds = {d: float(np.clip(rng.normal(18, 7), 3, None)) for d in uniq}
        base_dir = np.array([dd[d] for d in days])
        base_spd = np.array([ds[d] for d in days])
        wd = (base_dir + 20*np.sin(2*np.pi*np.arange(len(times))/24) + rng.normal(0, 10, len(times))) % 360
        ws = np.clip(base_spd + rng.normal(0, 3, len(times)), 0, None)
        store[(round(la,WX_DP),round(lo,WX_DP))] = pd.DataFrame(dict(time=times, ws=ws, wd=wd))
    # build daily onshore@8am per cell; presence prob rises with prior-24h onshore
    rows = []
    for (la, lo), w in store.items():
        w = w.assign(onsh=onshore(w["ws"].values, w["wd"].values)).set_index("time")
        for d in pd.date_range("2022-01-01", "2024-12-31", freq="D"):
            t = pd.Timestamp(d) + pd.Timedelta(hours=DEFAULT_HR)
            o24 = w.loc[t-pd.Timedelta(hours=24):t, "onsh"].mean()
            p = 1/(1+math.exp(-(o24-9)/3))         # planted logistic link
            if rng.random() < 0.18*p:              # sparse presence
                rows.append(dict(cluster=(la,lo), date=d.date(), lat=la, lng=lo,
                                 ldt=t.tz_localize("Australia/Sydney"), y=1))
    pres = pd.DataFrame(rows)
    # matched background
    bg=[]; span=pd.date_range("2022-01-01","2024-12-31",freq="D")
    for cl,g in pres.groupby("cluster"):
        excl={d for d0 in g["date"] for d in
              [d0+dt.timedelta(days=k) for k in range(-BG_EXCLUDE,BG_EXCLUDE+1)]}
        pool=[d for d in span if d.date() not in excl]
        pick=rng.choice(len(pool),size=len(g)*BG_PER_CASE,replace=False)
        for i in pick:
            bg.append(dict(cluster=cl,date=pool[i].date(),lat=cl[0],lng=cl[1],
                           ldt=(pool[i]+pd.Timedelta(hours=DEFAULT_HR)).tz_localize("Australia/Sydney"),y=0))
    samp=pd.concat([pres,pd.DataFrame(bg)],ignore_index=True)
    print(f"  synth presence={len(pres)} background={len(bg)}")
    F=add_features(samp,store,None)
    report,res=evaluate(F); print(report)
    assert res["auc_wind"].mean()>0.65, "selftest failed: wind signal not recovered"
    assert res["ors"].filter(like="onsh").max()>1.0, "selftest failed: onshore OR<=1"
    print("\nSELFTEST PASSED ✔  pipeline recovers a planted signal.")

# ---- main -------------------------------------------------------------------
def main():
    if "--selftest" in sys.argv:
        selftest(); return
    print("[1/5] pull observations"); obs = pull_obs(); obs.to_csv("obs_sydney.csv", index=False)
    print("[2/5] presence-days + matched background"); samp = make_samples(obs)
    print("[3/5] fetch weather (cached)"); store = build_weather(samp)
    print("[4/5] per-beach aspect + lag features")
    aspect_map = build_aspect(samp)
    pd.DataFrame([{"cluster": str(k), "aspect_deg": round(v, 1)} for k, v in sorted(aspect_map.items())]
                 ).to_csv("aspect_table.csv", index=False)
    print(f"  aspects: {len(aspect_map)} clusters, "
          f"{sum(1 for v in aspect_map.values() if v != OFFSHORE_BRG)} non-default")
    F = add_features(samp, store, aspect_map); F.to_csv("modelling_table.csv", index=False)
    print("[5/5] models + blocked CV"); report, res = evaluate(F)
    open("phase1_report.txt", "w").write(report); print("\n" + report)
    plots(res)
    print("\nwrote: obs_sydney.csv, aspect_table.csv, modelling_table.csv, phase1_report.txt, phase1_plot.png")

if __name__ == "__main__":
    main()