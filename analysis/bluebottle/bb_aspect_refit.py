#!/usr/bin/env python3
"""
bb_aspect_refit.py  (v2)  â€”  STEP 2, runs ANYWHERE (your PC, or uploaded into the chat).

Fixes vs v1:
  * FOLDS BY CASE-SET, not by row date. A case + its 3 controls share the case's
    year and always land in the same fold, so every fold keeps ~1:3 balance and the
    per-year AUCs are meaningful. (v1 scattered a case's controls across years, which
    is why 2012 read 0.198 â€” a fold that was almost all other-year controls.)
  * VECTORIZED (numpy). The aspect grid search is now a handful of array passes, so
    the whole thing runs in ~1s instead of minutes.
  * DEFAULT FEATURE = day-max of the trailing-3h onshore (the app's own day-band
    logic), which doesn't depend on iNaturalist's unreliable observed minute. The
    observed-hour feature is still computed and reported for scheme A as a comparison.

Input : bb_windjoined.csv        Deps : pip install pandas numpy
Run   : python bb_aspect_refit.py

>>> DECISION markers = calls the refined-analysis session should own.
"""

import json, sys, warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=RuntimeWarning)   # nanmax/nanmean on all-NaN edges

BASELINE_ASPECT = 82.0
GRID = np.arange(0, 181, 5.0)      # >>> DECISION: aspect search grid for scheme C
MIN_CASES_FIT   = 5                # >>> DECISION: geographic fallback below this many train cases
MIN_YEAR        = None             # >>> DECISION: e.g. 2015 to drop noisy low-effort early years


# ---------- feature machinery (vectorized) ----------
# block layout: 30 hours [day-1 18:00 .. day 23:00]; index 6 == day 00:00,
# so hour H sits at index 6+H and its trailing-3h uses cols [4+H, 5+H, 6+H].
def load(path):
    df = pd.read_csv(path)
    if MIN_YEAR is not None:
        df = df[df.year >= MIN_YEAR].reset_index(drop=True)
    n = len(df)
    SPD = np.full((n, 30), np.nan); DIR = np.full((n, 30), np.nan)
    for i, (s, d) in enumerate(zip(df.blk_spd, df.blk_dir)):
        SPD[i] = [np.nan if v is None else v for v in json.loads(s)]
        DIR[i] = [np.nan if v is None else v for v in json.loads(d)]
    hour = pd.to_numeric(df.obs_hour, errors="coerce")
    hour = np.where(np.isfinite(hour), hour, -1).astype(int)
    return df, SPD, DIR, hour


def windows(SPD, DIR, aspect):
    """[n,24] trailing-3h mean onshore, one column per hour-of-day."""
    ons = np.maximum(0.0, np.cos(np.radians(DIR - aspect))) * SPD      # [n,30]
    W = np.empty((SPD.shape[0], 24))
    for H in range(24):
        W[:, H] = np.nanmean(ons[:, 4 + H:7 + H], axis=1)
    return W


def feat_daymax(SPD, DIR, aspect):
    return np.nanmax(windows(SPD, DIR, aspect), axis=1)


def feat_obshour(SPD, DIR, aspect, hour):
    W = windows(SPD, DIR, aspect)
    out = np.nanmax(W, axis=1)
    known = hour >= 0
    out[known] = W[np.arange(len(hour))[known], hour[known]]
    return out


# ---------- metric ----------
def auc(labels, scores):
    labels = np.asarray(labels); scores = np.asarray(scores, float)
    ok = ~np.isnan(scores); labels, scores = labels[ok], scores[ok]
    pos = labels == 1; npos = pos.sum(); nneg = (~pos).sum()
    if npos == 0 or nneg == 0:
        return np.nan
    _, inv, cnt = np.unique(scores, return_inverse=True, return_counts=True)
    csum = np.cumsum(cnt); ranks = ((csum - cnt) + csum + 1) / 2.0
    r = ranks[inv]
    return (r[pos].sum() - npos * (npos + 1) / 2) / (npos * nneg)


# ---------- schemes ----------
def geo_map(df):
    return dict(zip(df.beach, df.shore_normal))


def per_beach_scores(df, SPD, DIR, aspect_of):
    out = np.empty(len(df))
    for beach, idx in df.groupby("beach").groups.items():
        idx = np.array(list(idx))
        out[idx] = feat_daymax(SPD[idx], DIR[idx], aspect_of(beach))
    return out


def fit_aspects(df, SPD, DIR):
    """Per-beach aspect maximising TRAIN day-max AUC. Leak-safe: train fold only."""
    out = {}
    for beach, idx in df.groupby("beach").groups.items():
        idx = np.array(list(idx)); y = df.is_case.to_numpy()[idx]
        geo = float(df.shore_normal.to_numpy()[idx][0])
        if (y == 1).sum() < MIN_CASES_FIT:
            out[beach] = geo; continue
        best_a, best_s = geo, -1.0
        for a in GRID:
            s = auc(y, feat_daymax(SPD[idx], DIR[idx], a))
            if not np.isnan(s) and s > best_s:
                best_s, best_a = s, a
        out[beach] = best_a
    return out


def year_blocked(df, SPD, DIR):
    # fold key = the CASE's year, shared by its controls (keeps matched sets together)
    case_year = df[df.is_case == 1].set_index("case_id").year.to_dict()
    fold = df.case_id.map(case_year).to_numpy()
    years = sorted(set(fold))
    geo = geo_map(df)
    poolY, poolA, poolB, poolC, per_year = [], [], [], [], []
    for ty in years:
        te = np.where(fold == ty)[0]; tr = np.where(fold != ty)[0]
        yte = df.is_case.to_numpy()[te]
        if (yte == 1).sum() == 0 or (yte == 0).sum() == 0:
            continue
        dte = df.iloc[te].reset_index(drop=True)
        sA = feat_daymax(SPD[te], DIR[te], BASELINE_ASPECT)
        sB = per_beach_scores(dte, SPD[te], DIR[te], lambda b: geo.get(b, BASELINE_ASPECT))
        fitted = fit_aspects(df.iloc[tr].reset_index(drop=True), SPD[tr], DIR[tr])
        sC = per_beach_scores(dte, SPD[te], DIR[te], lambda b: fitted.get(b, BASELINE_ASPECT))
        per_year.append((ty, len(te), int((yte == 1).sum()),
                         auc(yte, sA), auc(yte, sB), auc(yte, sC)))
        poolY += list(yte); poolA += list(sA); poolB += list(sB); poolC += list(sC)
    return per_year, (np.array(poolY), np.array(poolA), np.array(poolB), np.array(poolC))


def main():
    try:
        df, SPD, DIR, hour = load("bb_windjoined.csv")
    except FileNotFoundError:
        sys.exit("bb_windjoined.csv not found â€” run bb_wind_join.py first (on your PC).")

    nc = int((df.is_case == 1).sum())
    print(f"Loaded {len(df)} rows â€” {nc} cases, {len(df)-nc} controls, "
          f"{df.beach.nunique()} beaches, case-years "
          f"{df[df.is_case==1].year.min()}-{df[df.is_case==1].year.max()}")
    if MIN_YEAR: print(f"(restricted to year >= {MIN_YEAR})")

    a_dm = auc(df.is_case, feat_daymax(SPD, DIR, BASELINE_ASPECT))
    a_oh = auc(df.is_case, feat_obshour(SPD, DIR, BASELINE_ASPECT, hour))
    print(f"\nFeature check (single 82 deg, whole set): "
          f"day-max AUC {a_dm:.3f}  vs  observed-hour AUC {a_oh:.3f}")

    per_year, (y, sA, sB, sC) = year_blocked(df, SPD, DIR)
    print("\nPooled held-out AUC (year-blocked by case-set, day-max feature):")
    print(f"  A  single 82 deg      : {auc(y, sA):.3f}")
    print(f"  B  geographic aspect  : {auc(y, sB):.3f}   (delta {auc(y,sB)-auc(y,sA):+.3f})")
    print(f"  C  fitted per-beach   : {auc(y, sC):.3f}   (delta {auc(y,sC)-auc(y,sA):+.3f})")
    print("\nPer-year (n rows, n cases, A, B, C):")
    for ty, n, ncase, a, b, c in per_year:
        print(f"  {ty}: n={n:4d} cases={ncase:3d}  A={a:.3f}  B={b:.3f}  C={c:.3f}")

    print("\nPer-beach case counts (why C is fragile):")
    vc = df[df.is_case == 1].beach.value_counts()
    print("  " + ", ".join(f"{b}:{n}" for b, n in vc.items()))

    print("\nRead:")
    print("  * B - A near zero  -> per-beach aspect isn't worth shipping; single 82 deg holds.")
    print("  * day-max >> observed-hour would confirm iNat times are noise; use day-max.")
    print("  * >>> DECISION: add month+abundance via logistic/GBM at year_blocked(), same folds.")
    print("  * Calibration (presence->absolute) needs the 'none seen' taps â€” not in this file.")


if __name__ == "__main__":
    main()


