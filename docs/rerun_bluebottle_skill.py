"""Cabbage Tree Bay bluebottle skill test (9 Oct 2026).

Reproduces every number in the "Does it work?" section of
bluebottle-model.html. Python 3, standard library only.

  python docs/rerun_bluebottle_skill.py                 # open-ocean ERA5 cell, 800 m radius (the page)
  python docs/rerun_bluebottle_skill.py coast 0.5       # coastal cell, 500 m radius

ERA5 hourly wind is fetched once from the Open-Meteo archive API (free, no key) into
docs/wx_cache/ (not committed), then read from there.

Cases  : iNaturalist Physalia records within RADIUS_KM of the bay centre, collapsed
         to distinct sighting-days; the case time is the day's earliest observation
         time (Sydney local), noon when no time is recorded.
Wind   : ERA5 hourly 10 m wind from Open-Meteo, Sydney local time.
Score  : onshore(t) = mean over the W hourly readings ending at t of
         speed * max(0, cos(dir - aspect)).  The app runs aspect 82, W = 3.
Controls (two designs, both at the SAME clock hour as the case, so the sea-breeze
cycle cancels):
  'all'    : every day of the record (2010-2026) except +/-3 days of any case.
  'season' : only days within +/-30 days of the case's day-of-year (any year),
             same exclusion. This removes the seasonal wind climatology, so what
             remains is the day-to-day wind signal alone.
AUC = P(case score > control score), ties count half (Mann-Whitney).
"""
import json, math, sys, random, datetime as dt, csv, os

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, 'data', 'obs_sydney.csv')
GRID = sys.argv[1] if len(sys.argv) > 1 else 'off'     # 'off' = 33.75S 151.50E (open ocean), 'coast' = 33.75S 151.25E
RADIUS_KM = float(sys.argv[2]) if len(sys.argv) > 2 else 0.8
CENTRE = (-33.7995, 151.2930)

def hav(a, b, c, d):
    r = math.pi / 180
    x = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(x))

# ---- wind
spd, dirn, times = [], [], []
CELL = {'off': (-33.80, 151.50), 'coast': (-33.80, 151.30)}[GRID]
CACHE = os.path.join(HERE, 'wx_cache')
os.makedirs(CACHE, exist_ok=True)
for y in (2010, 2014, 2018, 2022):
    fn = os.path.join(CACHE, f'era5_{GRID}_{y}.json')
    if not os.path.exists(fn):
        end = f'{y + 3}-12-31' if y + 3 < 2025 else '2026-10-07'
        url = ('https://archive-api.open-meteo.com/v1/archive?latitude=%.2f&longitude=%.2f'
               '&start_date=%d-01-01&end_date=%s&hourly=wind_speed_10m,wind_direction_10m'
               '&wind_speed_unit=kmh&timezone=Australia%%2FSydney&models=era5') % (CELL[0], CELL[1], y, end)
        import urllib.request
        raw = urllib.request.urlopen(url, timeout=300).read()
        json.loads(raw)                      # refuse to cache a bad download
        with open(fn, 'wb') as f:
            f.write(raw)
    j = json.load(open(fn))
    h = j['hourly']
    times += h['time']; spd += h['wind_speed_10m']; dirn += h['wind_direction_10m']
last = max(i for i in range(len(times)) if spd[i] is not None and dirn[i] is not None)
nmiss = sum(1 for i in range(last + 1) if spd[i] is None or dirn[i] is None)
print('missing hours before the last valid one:', nmiss)
times, spd, dirn = times[:last + 1], spd[:last + 1], dirn[:last + 1]
spd = [s if s is not None else 0.0 for s in spd]
dirn = [d if d is not None else 0.0 for d in dirn]
idx = {t: i for i, t in enumerate(times)}
N = len(times)
print('wind hours', N, times[0], times[-1], 'grid', GRID)

# ---- cases
rows = list(csv.DictReader(open(CSV, encoding='utf-8')))
def local_hour(r):
    t = r.get('time_observed_at') or ''
    if not t:
        return None
    try:
        d = dt.datetime.fromisoformat(t)
    except ValueError:
        return None
    # convert to Sydney local: AEST +10 / AEDT +11 (first Sun Oct -> first Sun Apr)
    u = d.astimezone(dt.timezone.utc)
    def first_sun(y, m):
        d0 = dt.date(y, m, 1)
        return d0 + dt.timedelta(days=(6 - d0.weekday()) % 7)
    y = u.year
    # DST starts 2am AEST first Sun Oct (=16:00 UTC Sat), ends 3am AEDT first Sun Apr (=16:00 UTC Sat)
    start = dt.datetime.combine(first_sun(y, 10), dt.time(0), tzinfo=dt.timezone.utc) - dt.timedelta(hours=8)
    end = dt.datetime.combine(first_sun(y, 4), dt.time(0), tzinfo=dt.timezone.utc) - dt.timedelta(hours=8)
    off = 11 if (u >= start or u < end) else 10
    return (u + dt.timedelta(hours=off))

cases = {}
for r in rows:
    if not (r['observed_on'] or '')[:4].isdigit():
        continue
    if hav(float(r['lat']), float(r['lng']), *CENTRE) > RADIUS_KM:
        continue
    day = r['observed_on'][:10]
    lt = local_hour(r)
    hr = lt.hour if (lt and lt.strftime('%Y-%m-%d') == day) else None
    c = cases.setdefault(day, {'n': 0, 'hr': None})
    c['n'] += 1
    if hr is not None and (c['hr'] is None or hr < c['hr']):
        c['hr'] = hr
case_days = sorted(d for d in cases if d >= times[0][:10])
for d in case_days:
    if cases[d]['hr'] is None:
        cases[d]['hr'] = 12
print('records', sum(cases[d]['n'] for d in case_days), 'case days', len(case_days),
      case_days[0], case_days[-1], 'no-time days', sum(1 for d in case_days if cases[d]['hr'] == 12))

def onshore_at(i, aspect, W, lag=0):
    j1 = i - lag
    j0 = j1 - W + 1
    if j0 < 0:
        return None
    s = 0.0
    for j in range(j0, j1 + 1):
        s += spd[j] * max(0.0, math.cos(math.radians(dirn[j] - aspect)))
    return s / W

def iso(day, hr):
    return f'{day}T{hr:02d}:00'

# exclusion: +/-3 days of any case
case_dates = [dt.date.fromisoformat(d) for d in case_days]
excl = set()
for cd in case_dates:
    for k in range(-3, 4):
        excl.add(cd + dt.timedelta(days=k))
all_days = sorted({t[:10] for t in times})
all_dates = [dt.date.fromisoformat(d) for d in all_days if dt.date.fromisoformat(d) not in excl]

def doy_dist(a, b):
    x = abs(a.timetuple().tm_yday - b.timetuple().tm_yday)
    return min(x, 365 - x)

control_sets = {}
for d in case_days:
    cd = dt.date.fromisoformat(d)
    control_sets[d] = {
        'all': all_dates,
        'season': [x for x in all_dates if doy_dist(x, cd) <= 30],
    }

_pref = {}
def prefix(aspect):
    if aspect not in _pref:
        P = [0.0]
        c = math.radians(aspect)
        for j in range(N):
            P.append(P[-1] + spd[j] * max(0.0, math.cos(math.radians(dirn[j]) - c)))
        _pref[aspect] = P
    return _pref[aspect]

def fast_on(P, i, W, lag):
    j1 = i - lag
    j0 = j1 - W + 1
    if j0 < 0:
        return None
    return (P[j1 + 1] - P[j0]) / W

_ctl_idx = {}
def scores(aspect, W, lag=0, design='all'):
    """returns list of (case_score, [control scores])"""
    P = prefix(aspect)
    out = []
    for d in case_days:
        hr = cases[d]['hr']
        i = idx.get(iso(d, hr))
        if i is None:
            continue
        cs = fast_on(P, i, W, lag)
        if cs is None:
            continue
        key = (d, design)
        if key not in _ctl_idx:
            _ctl_idx[key] = [k for k in (idx.get(iso(x.isoformat(), hr)) for x in control_sets[d][design]) if k is not None]
        ctl = [v for v in (fast_on(P, k, W, lag) for k in _ctl_idx[key]) if v is not None]
        out.append((cs, ctl))
    return out

def auc(pairs):
    num = den = 0.0
    for cs, ctl in pairs:
        if not ctl:
            continue
        gt = sum(1 for v in ctl if cs > v)
        eq = sum(1 for v in ctl if cs == v)
        num += (gt + 0.5 * eq) / len(ctl)
        den += 1
    return num / den

def boot(pairs, B=2000, seed=7):
    rnd = random.Random(seed)
    per = []
    for cs, ctl in pairs:
        gt = sum(1 for v in ctl if cs > v); eq = sum(1 for v in ctl if cs == v)
        per.append((gt + 0.5 * eq) / len(ctl))
    vals = []
    for _ in range(B):
        s = [per[rnd.randrange(len(per))] for _ in per]
        vals.append(sum(s) / len(s))
    vals.sort()
    return vals[int(0.05 * B)], vals[int(0.95 * B) - 1]

R = {'grid': GRID, 'radius_km': RADIUS_KM, 'n_case_days': len(case_days),
     'n_records': sum(cases[d]['n'] for d in case_days),
     'first': case_days[0], 'last': case_days[-1]}

for design in ('all', 'season'):
    p = scores(82, 3, 0, design)
    a = auc(p); lo, hi = boot(p)
    R[f'auc_{design}'] = [round(a, 3), round(lo, 3), round(hi, 3)]
    print(f'AUC aspect82 W3 [{design}]: {a:.3f}  90% CI {lo:.3f}-{hi:.3f}  (n={len(p)})')

# window sweep
R['window'] = {}
for design in ('all', 'season'):
    R['window'][design] = []
    for W in (1, 2, 3, 6, 9, 12, 24, 48):
        a = auc(scores(82, W, 0, design))
        R['window'][design].append([W, round(a, 3)])
    print('window', design, R['window'][design])

# lag shift (3 h window ending L hours before the sighting)
R['lag'] = {}
for design in ('all', 'season'):
    R['lag'][design] = []
    for L in (0, 3, 6, 9, 12, 18, 24, 36, 48, 72):
        a = auc(scores(82, 3, L, design))
        R['lag'][design].append([L, round(a, 3)])
    print('lag', design, R['lag'][design])

# aspect sweep
R['aspect'] = {}
for design in ('all', 'season'):
    R['aspect'][design] = []
    for asp in range(0, 360, 5):
        a = auc(scores(asp, 3, 0, design))
        R['aspect'][design].append([asp, round(a, 3)])
    best = max(R['aspect'][design], key=lambda x: x[1])
    print('aspect', design, 'best', best, 'at82', [x for x in R['aspect'][design] if x[0] == 80 or x[0] == 85])

# distributions + band likelihood ratios (all-days controls, aspect 82, W 3)
p = scores(82, 3, 0, 'all')
case_vals = [cs for cs, _ in p]
ctl_pool = []
for cs, ctl in p:
    ctl_pool.extend(ctl)
R['case_vals'] = [round(v, 2) for v in case_vals]
# compress control distribution into a 0.5 km/h histogram (0..40)
edges = [x * 0.5 for x in range(0, 81)]
def hist(vals):
    h = [0] * (len(edges))
    for v in vals:
        k = min(len(edges) - 1, int(v / 0.5))
        h[k] += 1
    t = len(vals)
    return [round(c / t, 5) for c in h]
R['ctl_hist'] = hist(ctl_pool)
R['case_hist'] = hist(case_vals)
TH = [0, 2, 4, 6, 12, 1e9]
bands = []
for lo, hi in zip(TH[:-1], TH[1:]):
    cf = sum(1 for v in case_vals if lo < v <= hi or (lo == 0 and v == 0)) / len(case_vals)
    kf = sum(1 for v in ctl_pool if lo < v <= hi or (lo == 0 and v == 0)) / len(ctl_pool)
    nc = sum(1 for v in case_vals if lo < v <= hi or (lo == 0 and v == 0))
    bands.append([lo, hi if hi < 1e8 else None, nc, round(cf, 3), round(kf, 3), round(cf / kf, 2) if kf else None])
R['bands'] = bands
print('bands (lo,hi,n_case,case_frac,ctl_frac,LR):')
for b in bands:
    print('  ', b)
cs_sorted = sorted(case_vals)
q = lambda f: cs_sorted[min(len(cs_sorted) - 1, int(f * len(cs_sorted)))]
R['case_quartiles'] = [round(q(0.25), 1), round(q(0.5), 1), round(q(0.75), 1)]
ks = sorted(ctl_pool)
R['ctl_quartiles'] = [round(ks[int(0.25 * len(ks))], 1), round(ks[int(0.5 * len(ks))], 1), round(ks[int(0.75 * len(ks))], 1)]
print('case quartiles', R['case_quartiles'], 'control quartiles', R['ctl_quartiles'])

# wind vectors (3 h mean, meteorological FROM direction) for cases, and a control sample
def vec(i, W=3):
    u = v = 0.0
    for j in range(i - W + 1, i + 1):
        th = math.radians(dirn[j])
        u += spd[j] * math.sin(th); v += spd[j] * math.cos(th)   # vector pointing TO the FROM direction
    u /= W; v /= W
    return u, v
cv = []
for d in case_days:
    i = idx.get(iso(d, cases[d]['hr']))
    if i is not None:
        u, v = vec(i)
        cv.append([round(u, 2), round(v, 2), d])
rnd = random.Random(11)
kv = []
for _ in range(600):
    d = rnd.choice(case_days); x = rnd.choice(control_sets[d]['all'])
    k = idx.get(iso(x.isoformat(), cases[d]['hr']))
    if k is not None and k >= 3:
        u, v = vec(k); kv.append([round(u, 2), round(v, 2)])
R['case_vec'] = cv; R['ctl_vec'] = kv
R['case_days'] = [[d, cases[d]['n'], cases[d]['hr']] for d in case_days]

# ---- robustness: 3 random controls per case, plain speed
rnd3 = random.Random(3)
P82 = prefix(82)
dist3 = []
for _ in range(2000):
    num = 0.0; n = 0
    for d in case_days:
        hr = cases[d]['hr']; i = idx.get(iso(d, hr))
        if i is None: continue
        cs = fast_on(P82, i, 3, 0)
        ks = _ctl_idx[(d, 'all')]
        pick = [ks[rnd3.randrange(len(ks))] for _ in range(3)]
        vals = [fast_on(P82, k, 3, 0) for k in pick]
        num += sum(1.0 if cs > v else 0.5 if cs == v else 0.0 for v in vals) / 3; n += 1
    dist3.append(num / n)
dist3.sort()
R['auc_3ctl'] = [round(sum(dist3)/len(dist3),3), round(dist3[int(0.05*len(dist3))],3), round(dist3[int(0.95*len(dist3))],3)]
print('3-control design: mean %.3f, 90%% of draws %.3f-%.3f' % tuple(R['auc_3ctl'][:3]))
# plain speed (no direction) as a baseline
Ps = [0.0]
for j in range(N): Ps.append(Ps[-1] + spd[j])
def sp_scores(design):
    out = []
    for d in case_days:
        hr = cases[d]['hr']; i = idx.get(iso(d, hr))
        if i is None: continue
        out.append(((Ps[i+1]-Ps[i-2])/3, [(Ps[k+1]-Ps[k-2])/3 for k in _ctl_idx[(d, design)] if k >= 2]))
    return out
for design in ('all', 'season'):
    R['auc_speed_'+design] = round(auc(sp_scores(design)), 3)
    print('plain speed AUC', design, R['auc_speed_'+design])

# ---- operational model score: windPos x seasonMult (clamped 6..94), as index.html runs it
TH3 = [4, 6, 12]
SM = [1.0, 1.0, 0.9, 0.6, 0.35, 0.2, 0.15, 0.2, 0.9, 1.0, 1.0, 1.0]   # BBF_SEASON_MULT since 9 Oct 2026
def rawpos(o):
    bi = 0
    while bi < 3 and o > TH3[bi]: bi += 1
    lo = 0 if bi == 0 else TH3[bi-1]
    hi = TH3[bi] if bi < 3 else lo + 8
    frac = max(0.0, min(1.0, (o - lo) / (hi - lo)))
    return max(6.0, min(94.0, bi * 25 + frac * 25))
def modelpos(o, month0):
    return max(6.0, min(94.0, rawpos(o) * SM[month0]))
mpairs = []
for d in case_days:
    hr = cases[d]['hr']; i = idx.get(iso(d, hr))
    if i is None: continue
    cs = modelpos(fast_on(P82, i, 3, 0), int(d[5:7]) - 1)
    ctl = []
    for k in _ctl_idx[(d, 'all')]:
        ctl.append(modelpos(fast_on(P82, k, 3, 0), int(times[k][5:7]) - 1))
    mpairs.append((cs, ctl))
a = auc(mpairs); lo_, hi_ = boot(mpairs)
R['auc_model_all'] = [round(a, 3), round(lo_, 3), round(hi_, 3)]
print('operational model (wind x season) AUC vs all days: %.3f  90%% CI %.3f-%.3f' % (a, lo_, hi_))
# warning rates at the band edges of the operational score (High+ = pos >= 50, Moderate+ = pos >= 25)
mc = [cs for cs, _ in mpairs]; mk = [v for _, c in mpairs for v in c]
R['model_rates'] = {}
for name, edge in (('moderate_plus', 25), ('high_plus', 50), ('extreme', 75)):
    tpr = sum(1 for v in mc if v >= edge) / len(mc); fpr = sum(1 for v in mk if v >= edge) / len(mk)
    R['model_rates'][name] = [round(tpr, 3), round(fpr, 3)]
    print('  %s: sighting-days flagged %.2f, other days flagged %.2f' % (name, tpr, fpr))
# wind-only rates at onshore > 6 km/h
oc = [cs for cs, _ in p]; ok_ = [v for _, c in p for v in c]
R['wind_rates_gt6'] = [round(sum(1 for v in oc if v > 6)/len(oc), 3), round(sum(1 for v in ok_ if v > 6)/len(ok_), 3)]
print('  wind-only onshore>6: sighting-days %.2f, other days %.2f' % tuple(R['wind_rates_gt6']))
# Hewitt-style feature: daily mean onshore of the PREVIOUS calendar day
def prevday(dstr):
    d0 = (dt.date.fromisoformat(dstr) - dt.timedelta(days=1)).isoformat()
    i0 = idx.get(d0 + 'T00:00'); 
    if i0 is None or i0 + 24 > N: return None
    return (P82[i0 + 24] - P82[i0]) / 24
for design in ('all', 'season'):
    pp = []
    for d in case_days:
        cs = prevday(d)
        if cs is None: continue
        ctl = [v for v in (prevday(x.isoformat()) for x in control_sets[d][design]) if v is not None]
        pp.append((cs, ctl))
    R['auc_prevday_' + design] = round(auc(pp), 3)
    print('previous-day daily-mean onshore AUC', design, R['auc_prevday_' + design])
# ROC curves (pooled controls) for onshore (3 h) and the operational score
def roc(cv, kv, steps):
    out = []
    for t in steps:
        out.append([round(sum(1 for v in kv if v >= t)/len(kv), 4), round(sum(1 for v in cv if v >= t)/len(cv), 4)])
    return out
R['roc_onshore'] = roc(oc, ok_, [x * 0.5 for x in range(0, 80)] + [1e9])
R['roc_model'] = roc(mc, mk, list(range(0, 101, 2)) + [1e9])
# likelihood ratios on the SHIPPED band edges (0-4, 4-6, 6-12, >12) and the fitted ones (0-2, 2-6, 6-12, >12)
def lr(edges):
    res = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        inb = lambda v: (v <= hi if lo == 0 else lo < v <= hi)
        cf = sum(1 for v in oc if inb(v)) / len(oc); kf = sum(1 for v in ok_ if inb(v)) / len(ok_)
        res.append([lo, None if hi > 1e8 else hi, sum(1 for v in oc if inb(v)), round(cf, 3), round(kf, 3), round(cf / kf, 2) if kf else None])
    return res
R['lr_shipped'] = lr([0, 4, 6, 12, 1e9]); R['lr_fitted'] = lr([0, 2, 6, 12, 1e9])
print('LR shipped edges', R['lr_shipped']); print('LR fitted edges', R['lr_fitted'])
# monthly sighting-days at the bay
mon = [0]*12
for d in case_days: mon[int(d[5:7]) - 1] += 1
R['bay_months'] = mon
print('bay months', mon)

json.dump(R, open(os.path.join(CACHE, f'result_{GRID}_{RADIUS_KM}.json'), 'w'))
print('written')
