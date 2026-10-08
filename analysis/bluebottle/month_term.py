"""Job 2 of the handoff: does a month / abundance term lift held-out AUC?

Uses bb_aspect_refit.py's own loader, day-max feature and AUC, and its fold rule (fold = the
CASE's year, controls travel with their case). Everything fitted is fitted on the training folds
only. Scored pooled over all held-out folds, for all beaches and for the bay.
"""
import sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import bb_aspect_refit as R
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import GradientBoostingClassifier

df, SPD, DIR, hour = R.load('bb_windjoined.csv')
y = df.is_case.to_numpy()
month = df.month.to_numpy().astype(int)
wind = R.feat_daymax(SPD, DIR, 82.0)
SEASON = np.array([1.0, 1.0, 0.9, 0.6, 0.35, 0.2, 0.15, 0.2, 0.45, 0.75, 0.95, 1.0])   # BBF_SEASON_MULT

def rawpos(o):
    o = np.nan_to_num(o, nan=0.0)
    th = [4, 6, 12]
    bi = (o > 4).astype(int) + (o > 6) + (o > 12)
    lo = np.choose(bi, [0, 4, 6, 12]); hi = np.choose(bi, [4, 6, 12, 20])
    return np.clip(bi * 25 + np.clip((o - lo) / (hi - lo), 0, 1) * 25, 6, 94)

case_year = df[df.is_case == 1].set_index('case_id').year.to_dict()
fold = df.case_id.map(case_year).to_numpy()
ang = 2 * np.pi * (month - 0.5) / 12
X_sc = np.c_[np.sin(ang), np.cos(ang)]
w = np.nan_to_num(wind, nan=0.0)

scores = {k: np.full(len(df), np.nan) for k in
          ['wind', 'app: wind x shipped season', 'month only (logistic)', 'wind + month (logistic)',
           'wind x abundance learned in-fold', 'wind + month (boosted trees)']}
for ty in sorted(set(fold)):
    te = fold == ty; tr = ~te
    if y[te].min() == y[te].max():
        continue
    scores['wind'][te] = w[te]
    scores['app: wind x shipped season'][te] = rawpos(w[te]) * SEASON[month[te] - 1]
    m = LogisticRegression(max_iter=1000).fit(X_sc[tr], y[tr])
    scores['month only (logistic)'][te] = m.decision_function(X_sc[te])
    Xw = np.c_[w, X_sc]
    mu, sd = Xw[tr].mean(0), Xw[tr].std(0) + 1e-9
    m = LogisticRegression(max_iter=1000).fit((Xw[tr] - mu) / sd, y[tr])
    scores['wind + month (logistic)'][te] = m.decision_function((Xw[te] - mu) / sd)
    # abundance: case share / control share per month, from the training folds only (+1 smoothing)
    cm = np.bincount(month[tr][y[tr] == 1] - 1, minlength=12) + 1.0
    km = np.bincount(month[tr][y[tr] == 0] - 1, minlength=12) + 1.0
    ab = (cm / cm.sum()) / (km / km.sum()); ab = ab / ab.max()
    scores['wind x abundance learned in-fold'][te] = rawpos(w[te]) * ab[month[te] - 1]
    g = GradientBoostingClassifier(random_state=0, max_depth=2, n_estimators=150).fit(np.c_[w, month][tr], y[tr])
    scores['wind + month (boosted trees)'][te] = g.predict_proba(np.c_[w, month][te])[:, 1]

def boot(mask, s, n=2000):
    rng = np.random.default_rng(0)
    ids = df.case_id.to_numpy()[mask]; u = np.unique(ids)
    yy, ss = y[mask], s[mask]
    pos = {c: np.where(ids == c)[0] for c in u}
    out = []
    for _ in range(n):
        pick = np.concatenate([pos[c] for c in rng.choice(u, len(u))])
        out.append(R.auc(yy[pick], ss[pick]))
    return np.nanpercentile(out, [5, 95])

beach = df.beach.to_numpy()
sets = [('all 21 beaches', np.ones(len(df), bool)),
        ('Cabbage Tree Bay', beach == 'Cabbage Tree Bay'),
        ('Cabbage Tree Bay + Shelly', np.isin(beach, ['Cabbage Tree Bay', 'Shelly Beach']))]
for name, mask in sets:
    print(f'\n{name}: {int(y[mask].sum())} cases, {int((y[mask] == 0).sum())} controls (pooled held-out, year-blocked)')
    base = R.auc(y[mask], scores['wind'][mask])
    for k, s in scores.items():
        a = R.auc(y[mask], s[mask]); lo, hi = boot(mask, s)
        print(f'  {k:34s} {a:.3f}  (90% CI {lo:.3f}-{hi:.3f})  {"" if k == "wind" else f"{a - base:+.3f}"}')
