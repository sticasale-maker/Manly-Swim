"""Bluebottle brief, extra analyses (9 Oct 2026): where the original AUC 0.89 could come from,
and whether waves, swell or Stokes drift add to the wind. Reproduces Figs 5-7, 11 and the wave
table of bluebottle-model.html. Python 3, standard library only.

  python docs/rerun_bluebottle_extras.py

Data are fetched once from the Open-Meteo archives (free, no key) into docs/wx_cache/:
  ERA5 hourly wind, open-ocean cell 33.75S 151.50E (2010-2026)
  Historical Forecast hourly wind at the bay (2021-2026)
  ERA5-ocean waves, 34.0S 151.5E (2010-2026); high-resolution swell partition (2022 on)
"""
import json, math, csv, os, sys, random, datetime as dt, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'wx_cache')
CSV = os.path.join(HERE, 'data', 'obs_sydney.csv')
OM = 'timezone=Australia%2FSydney'

def fetch(path, url):
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        raw = urllib.request.urlopen(url, timeout=300).read()
        json.loads(raw)
        with open(path, 'wb') as f:
            f.write(raw)
    return path

def span(y, last):
    return f'start_date={y}-01-01&end_date=' + (f'{y + 3}-12-31' if y + 3 < 2025 else last)
for y in (2010, 2014, 2018, 2022):
    fetch(os.path.join(CACHE, f'off_{y}.json'), 'https://archive-api.open-meteo.com/v1/archive?latitude=-33.80&longitude=151.50&'
          + span(y, '2026-10-07') + '&hourly=wind_speed_10m,wind_direction_10m&wind_speed_unit=kmh&models=era5&' + OM)
    fetch(os.path.join(CACHE, 'marine', f'e_{y}.json'), 'https://marine-api.open-meteo.com/v1/marine?latitude=-33.80&longitude=151.40&'
          + span(y, '2026-10-07') + '&hourly=wave_height,wave_direction,wave_period&models=era5_ocean&' + OM)
fetch(os.path.join(CACHE, 'marine', 'm_2022.json'), 'https://marine-api.open-meteo.com/v1/marine?latitude=-33.80&longitude=151.40&'
      + span(2022, '2026-10-07') + '&hourly=wave_height,wave_direction,wave_period,swell_wave_height,swell_wave_direction,swell_wave_period,'
      'wind_wave_height,wind_wave_direction,wind_wave_period&' + OM)
for y in (2021, 2023, 2025):
    fetch(os.path.join(CACHE, 'hf', f'hf_{y}.json'), 'https://historical-forecast-api.open-meteo.com/v1/forecast?latitude=-33.80&longitude=151.29&'
          f'start_date={y}-01-01&end_date=' + (f'{y + 1}-12-31' if y + 1 < 2026 else '2026-10-07') + '&hourly=wind_speed_10m,wind_direction_10m&' + OM)

def load_wind(files):
    t, s, d = [], [], []
    for f in files:
        h = json.load(open(f))['hourly']
        t += h['time']; s += h['wind_speed_10m']; d += h['wind_direction_10m']
    last = max(i for i in range(len(t)) if s[i] is not None and d[i] is not None)
    t, s, d = t[:last + 1], s[:last + 1], d[:last + 1]
    s = [x if x is not None else 0.0 for x in s]; d = [x if x is not None else 0.0 for x in d]
    return t, s, d

ERA = load_wind([os.path.join(CACHE, f'off_{y}.json') for y in (2010, 2014, 2018, 2022)])
HF = load_wind([os.path.join(CACHE, 'hf', f'hf_{y}.json') for y in (2021, 2023, 2025)])

def hav(a, b, c, d):
    r = math.pi / 180
    x = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(x))

def local_dt(r):
    t = r.get('time_observed_at') or ''
    if not t:
        return None
    d = dt.datetime.fromisoformat(t)
    u = d.astimezone(dt.timezone.utc)
    def first_sun(y, m):
        d0 = dt.date(y, m, 1); return d0 + dt.timedelta(days=(6 - d0.weekday()) % 7)
    start = dt.datetime.combine(first_sun(u.year, 10), dt.time(0), tzinfo=dt.timezone.utc) - dt.timedelta(hours=8)
    end = dt.datetime.combine(first_sun(u.year, 4), dt.time(0), tzinfo=dt.timezone.utc) - dt.timedelta(hours=8)
    return u + dt.timedelta(hours=11 if (u >= start or u < end) else 10)

ROWS = [r for r in csv.DictReader(open(CSV, encoding='utf-8')) if (r['observed_on'] or '')[:4].isdigit()]

def cases_by(selector, first_day='2010-01-01', last_day='2026-12-31'):
    cs = {}
    for r in ROWS:
        if not selector(r):
            continue
        day = r['observed_on'][:10]
        if not (first_day <= day <= last_day):
            continue
        lt = local_dt(r)
        hr = lt.hour if (lt and lt.strftime('%Y-%m-%d') == day) else None
        c = cs.setdefault(day, {'n': 0, 'hr': None})
        c['n'] += 1
        if hr is not None and (c['hr'] is None or hr < c['hr']):
            c['hr'] = hr
    for d in cs:
        if cs[d]['hr'] is None:
            cs[d]['hr'] = 12
    return cs

C = (-33.7995, 151.2930)
radius = lambda km: (lambda r: hav(float(r['lat']), float(r['lng']), *C) <= km)
byname = lambda r: any(w in r['place_guess'].lower() for w in ('manly', 'shelly', 'cabbage', 'fairy'))

class W:
    def __init__(self, src, aspect=82):
        self.t, self.s, self.d = src
        self.idx = {x: i for i, x in enumerate(self.t)}
        c = math.radians(aspect)
        P = [0.0]
        for j in range(len(self.t)):
            P.append(P[-1] + self.s[j] * max(0.0, math.cos(math.radians(self.d[j]) - c)))
        self.P = P
        self.days = sorted({x[:10] for x in self.t})
    def on(self, i, Wn=3):
        if i is None or i - Wn + 1 < 0: return None
        return (self.P[i + 1] - self.P[i - Wn + 1]) / Wn
    def at(self, day, hr, Wn=3):
        return self.on(self.idx.get(f'{day}T{hr:02d}:00'), Wn)
    def daymax(self, day, Wn=3, upto=23):
        v = [self.at(day, h, Wn) for h in range(0, upto + 1)]
        v = [x for x in v if x is not None]
        return max(v) if v else None
    def daymean(self, day):
        i = self.idx.get(f'{day}T00:00')
        if i is None or i + 24 > len(self.t): return None
        return (self.P[i + 24] - self.P[i]) / 24

def auc(case_vals, ctl_lists):
    num = n = 0.0
    for cs, ctl in zip(case_vals, ctl_lists):
        if cs is None or not ctl: continue
        num += (sum(1 for v in ctl if cs > v) + 0.5 * sum(1 for v in ctl if cs == v)) / len(ctl); n += 1
    return num / n, int(n)

def run(name, cases, w, case_fn, ctl_fn, ctl_days=None):
    days = sorted(d for d in cases if d >= w.days[0] and d <= w.days[-1])
    excl = set()
    for d in days:
        cd = dt.date.fromisoformat(d)
        for k in range(-3, 4): excl.add((cd + dt.timedelta(days=k)).isoformat())
    pool = [d for d in (ctl_days or w.days) if d not in excl]
    cv, kl = [], []
    for d in days:
        cv.append(case_fn(w, d, cases[d]['hr']))
        kl.append([v for v in (ctl_fn(w, x, cases[d]['hr']) for x in pool) if v is not None])
    a, n = auc(cv, kl)
    print(f'{a:.3f}  n={n:3d}  {name}')
    return a

print('=== Part 1: the same test, built several ways (AUC, n)')
if True:
    rnd = random.Random(5)
    E = W(ERA); H = W(HF)
    bay = cases_by(radius(0.8))
    same_hr = lambda w, d, h: w.at(d, h)
    print('--- baseline')
    run('baseline: ERA5, 800 m, same clock hour', bay, E, same_hr, same_hr)
    print('--- case set')
    for km in (0.5, 1.0, 1.5, 2.0):
        run(f'radius {km} km', cases_by(radius(km)), E, same_hr, same_hr)
    run('place name Manly/Shelly/Cabbage/Fairy', cases_by(byname), E, same_hr, same_hr)
    run('800 m, 2021-26 only', cases_by(radius(0.8), '2021-01-01'), E, same_hr, same_hr)
    print('--- control timing')
    run('controls at a RANDOM hour (incl. night)', bay, E, same_hr, lambda w, x, h: w.at(x, rnd.randrange(24)))
    run('controls at noon', bay, E, same_hr, lambda w, x, h: w.at(x, 12))
    run('controls at 09:00', bay, E, same_hr, lambda w, x, h: w.at(x, 9))
    run('controls = daily mean onshore', bay, E, same_hr, lambda w, x, h: w.daymean(x))
    print('--- case timing / aggregation')
    run('case = day max of 3 h window; controls same-hour', bay, E, lambda w, d, h: w.daymax(d), same_hr)
    run('case AND control = day max of 3 h window', bay, E, lambda w, d, h: w.daymax(d), lambda w, x, h: w.daymax(x))
    run('case = max up to obs time; controls = max up to same hour', bay, E, lambda w, d, h: w.daymax(d, upto=h), lambda w, x, h: w.daymax(x, upto=h))
    print('--- wind source')
    run('Historical Forecast wind (2021-26), same hour', cases_by(radius(0.8), '2021-01-01'), H, same_hr, same_hr)
    run('Historical Forecast, controls random hour', cases_by(radius(0.8), '2021-01-01'), H, same_hr, lambda w, x, h: w.at(x, rnd.randrange(24)))
    print('--- combinations')
    run('random-hour controls + day-max cases', bay, E, lambda w, d, h: w.daymax(d), lambda w, x, h: w.at(x, rnd.randrange(24)))
    run('random-hour controls + 1.5 km', cases_by(radius(1.5)), E, same_hr, lambda w, x, h: w.at(x, rnd.randrange(24)))
    # season-restricted controls (warm half only) - the opposite direction, for scale
    warm = [d for d in E.days if d[5:7] in ('10', '11', '12', '01', '02', '03')]
    run('controls from Oct-Mar only (same hour)', bay, E, same_hr, same_hr, warm)

print('=== Part 2: waves, swell and Stokes drift')
G = 9.81

def load(files, keys):
    t = []; out = {k: [] for k in keys}
    for f in files:
        h = json.load(open(f))['hourly']
        t += h['time']
        for k in keys: out[k] += h[k]
    return t, out

et, ev = load([os.path.join(CACHE, 'marine', f'e_{y}.json') for y in (2010, 2014, 2018, 2022)], ['wave_height', 'wave_direction', 'wave_period'])
st, sv = load([os.path.join(CACHE, 'marine', 'm_2022.json')], ['swell_wave_height', 'swell_wave_direction', 'swell_wave_period', 'wind_wave_height', 'wind_wave_direction', 'wind_wave_period'])
eidx = {x: i for i, x in enumerate(et)}; sidx = {x: i for i, x in enumerate(st)}

def stokes(hs, T):
    if hs is None or T is None or T <= 0: return None
    return math.pi ** 3 * hs * hs / (G * T ** 3)       # m/s

def onshore(v, d, asp=82):
    return None if v is None or d is None else v * max(0.0, math.cos(math.radians(d - asp)))

E = W(ERA)
cases = cases_by(radius(0.8))
days = sorted(cases)

def excl_pool(alldays):
    ex = set()
    for d in days:
        cd = dt.date.fromisoformat(d)
        for k in range(-3, 4): ex.add((cd + dt.timedelta(days=k)).isoformat())
    return [d for d in alldays if d not in ex]

edays = sorted({x[:10] for x in et}); pool = excl_pool(edays)
def doy(a, b):
    x = abs(dt.date.fromisoformat(a).timetuple().tm_yday - dt.date.fromisoformat(b).timetuple().tm_yday); return min(x, 365 - x)

def feat_era(name):
    def f(day, hr):
        i = eidx.get(f'{day}T{hr:02d}:00')
        if i is None: return None
        hs, dr, T = ev['wave_height'][i], ev['wave_direction'][i], ev['wave_period'][i]
        if hs is None: return None
        us = stokes(hs, T)
        if name == 'hs': return hs
        if name == 'hs_on': return onshore(hs, dr)
        if name == 'stokes': return us
        if name == 'stokes_on': return onshore(us, dr)
        if name == 'period': return T
        w = E.at(day, hr)
        if w is None: return None
        wind_drift = 0.02 * w / 3.6                       # m/s, 2 % of the onshore wind
        if name == 'wind': return w
        if name == 'drift_sum': return wind_drift + (onshore(us, dr) or 0.0)
        if name == 'ratio': return (onshore(us, dr) or 0.0) / wind_drift if wind_drift > 0.005 else None
    return f

def auc_of(fn, design='all', ds=None, pl=None):
    ds = ds or days; pl = pl or pool
    num = n = 0.0
    for d in ds:
        hr = cases[d]['hr']; cs = fn(d, hr)
        if cs is None: continue
        cand = pl if design == 'all' else [x for x in pl if doy(x, d) <= 30]
        ctl = [v for v in (fn(x, hr) for x in cand) if v is not None]
        if not ctl: continue
        num += (sum(1 for v in ctl if cs > v) + 0.5 * sum(1 for v in ctl if cs == v)) / len(ctl); n += 1
    return round(num / n, 3), int(n)

R = {}
print('ERA5 total waves, 2010-2026, bay cases at the observation hour')
for k, lab in [('wind', 'wind onshore 3 h (reference)'), ('hs', 'Hs, any direction'), ('hs_on', 'Hs x onshore cos'),
               ('period', 'mean period'), ('stokes', 'Stokes surface drift, any direction'),
               ('stokes_on', 'Stokes drift, onshore component'), ('drift_sum', 'wind drift (2 %) + onshore Stokes')]:
    a1 = auc_of(feat_era(k), 'all'); a2 = auc_of(feat_era(k), 'season')
    R['auc_' + k] = [a1[0], a2[0]]
    print(f'  {lab:40s} every day {a1[0]:.3f}   same season {a2[0]:.3f}   n={a1[1]}')

# magnitude: onshore Stokes vs onshore wind drift, all hours and sighting-days
def mags(ds, hrs):
    out = []
    for d, hr in zip(ds, hrs):
        i = eidx.get(f'{d}T{hr:02d}:00'); w = E.at(d, hr)
        if i is None or w is None or ev['wave_height'][i] is None: continue
        us = onshore(stokes(ev['wave_height'][i], ev['wave_period'][i]), ev['wave_direction'][i])
        out.append((0.017 * w / 3.6, 0.0266 * w / 3.6, us))
    return out
mc = mags(days, [cases[d]['hr'] for d in days])
rnd = random.Random(2)
samp = [rnd.choice(pool) for _ in range(4000)]
ma = mags(samp, [cases[rnd.choice(days)]['hr'] for _ in samp])
def med(v): v = sorted(v); return v[len(v) // 2]
R['mag_cases'] = {'wind_lo': med([m[0] for m in mc]), 'wind_hi': med([m[1] for m in mc]), 'stokes': med([m[2] for m in mc])}
R['mag_all'] = {'wind_lo': med([m[0] for m in ma]), 'wind_hi': med([m[1] for m in ma]), 'stokes': med([m[2] for m in ma])}
print('median onshore drift, sighting-days (cm/s): wind 1.7 %% %.1f, 2.66 %% %.1f, Stokes %.2f' % tuple(100 * R['mag_cases'][k] for k in ('wind_lo', 'wind_hi', 'stokes')))
print('median onshore drift, all days     (cm/s): wind 1.7 %% %.1f, 2.66 %% %.1f, Stokes %.2f' % tuple(100 * R['mag_all'][k] for k in ('wind_lo', 'wind_hi', 'stokes')))
share = [m[2] / (m[0] + m[2]) for m in mc if (m[0] + m[2]) > 0]
print('Stokes share of (1.7 %% wind drift + Stokes) on sighting-days: median %.0f %%, max %.0f %%' % (100 * med(share), 100 * max(share)))
R['stokes_share_med'] = med(share); R['stokes_share_max'] = max(share)
R['case_pairs'] = [[round(100 * m[0], 2), round(100 * m[1], 2), round(100 * m[2], 3)] for m in mc]

# swell partition (late 2021 on) at the high-res cell
sdays = sorted({x[:10] for x in st if sv['swell_wave_height'][sidx[x]] is not None})
sc = [d for d in days if sdays and d >= sdays[0]]
spool = excl_pool(sdays)
def sfeat(name):
    def f(day, hr):
        i = sidx.get(f'{day}T{hr:02d}:00')
        if i is None: return None
        hs, dr, T = sv['swell_wave_height'][i], sv['swell_wave_direction'][i], sv['swell_wave_period'][i]
        if hs is None: return None
        if name == 'swell_hs': return hs
        if name == 'swell_on': return onshore(hs, dr)
        if name == 'swell_stokes_on': return onshore(stokes(hs, T), dr)
        if name == 'wind': return E.at(day, hr)
    return f
print(f'Swell partition, {sdays[0]} on ({len(sc)} bay sighting-days)')
for k in ('wind', 'swell_hs', 'swell_on', 'swell_stokes_on'):
    a1 = auc_of(sfeat(k), 'all', sc, spool)
    R['swell_auc_' + k] = a1[0]
    print(f'  {k:18s} every day {a1[0]:.3f}  n={a1[1]}')

# ---- roses: 16 sectors (FROM), speed / height classes. Climatology = every hour 2010-2026.
def sector(d): return int(((d + 11.25) % 360) // 22.5)
WCL = [0, 10, 20, 30, 1e9]                 # km/h
HCL = [0, 1, 1.5, 2, 1e9]                  # m
def rose(vals, classes):
    r = [[0] * (len(classes) - 1) for _ in range(16)]
    n = 0
    for v, d in vals:
        if v is None or d is None: continue
        n += 1
        c = next(k for k in range(len(classes) - 1) if v < classes[k + 1])
        r[sector(d)][c] += 1
    return [[round(x / n, 5) for x in row] for row in r], n
wt, ws, wd = ERA
R['wind_rose_all'], _ = rose(zip(ws, wd), WCL)
def wmean(i, n=3):
    u = v = 0.0
    for j in range(i - n + 1, i + 1):
        th = math.radians(wd[j]); u += ws[j] * math.sin(th); v += ws[j] * math.cos(th)
    u /= n; v /= n
    return math.hypot(u, v), (math.degrees(math.atan2(u, v)) + 360) % 360
widx = {x: i for i, x in enumerate(wt)}
cw = [wmean(widx[f'{d}T{cases[d]["hr"]:02d}:00']) for d in days if f'{d}T{cases[d]["hr"]:02d}:00' in widx]
R['wind_rose_cases'], R['n_wind_cases'] = rose(cw, WCL)
R['wind_cases'] = [[round(s, 1), round(d)] for s, d in cw]
R['wave_rose_all'], _ = rose(zip(ev['wave_height'], ev['wave_direction']), HCL)
cwv = []
for d in days:
    i = eidx.get(f'{d}T{cases[d]["hr"]:02d}:00')
    if i is not None and ev['wave_height'][i] is not None: cwv.append((ev['wave_height'][i], ev['wave_direction'][i]))
R['wave_rose_cases'], R['n_wave_cases'] = rose(cwv, HCL)
R['wave_cases'] = [[round(h, 2), round(d)] for h, d in cwv]
R['swell_rose_all'], R['n_swell_all'] = rose(zip(sv['swell_wave_height'], sv['swell_wave_direction']), HCL)
R['swell_first'] = sdays[0]

# swell-only rose at sighting-days since the partition starts, and swell vs wind-sea Stokes sizes
csw = []; sw_st = []; ww_st = []; wd_cm = []
for d in sc:
    hr = cases[d]['hr']; i = sidx.get(f'{d}T{hr:02d}:00')
    if i is None or sv['swell_wave_height'][i] is None: continue
    csw.append((sv['swell_wave_height'][i], sv['swell_wave_direction'][i]))
    sw_st.append(onshore(stokes(sv['swell_wave_height'][i], sv['swell_wave_period'][i]), sv['swell_wave_direction'][i]))
    ww = stokes(sv['wind_wave_height'][i], sv['wind_wave_period'][i])
    ww_st.append(onshore(ww, sv['wind_wave_direction'][i]) if ww is not None else 0.0)
    wd_cm.append(0.017 * (E.at(d, hr) or 0) / 3.6)
R['swell_rose_cases'], R['n_swell_cases'] = rose(csw, HCL)
R['swell_cases'] = [[round(h, 2), round(d)] for h, d in csw]
R['mag_swellperiod'] = {'swell_stokes': med(sw_st), 'windsea_stokes': med(ww_st), 'wind_lo': med(wd_cm)}
print('since 2022, sighting-days, median onshore cm/s: swell Stokes %.2f, wind-sea Stokes %.2f, wind drift 1.7%% %.1f' % (100*med(sw_st), 100*med(ww_st), 100*med(wd_cm)))
allsw = sorted(h for h in sv['swell_wave_height'] if h is not None)
print('swell Hs median all hours %.2f, sighting-days %.2f' % (allsw[len(allsw)//2], med([h for h, _ in csw])))
R['swell_hs_med'] = [allsw[len(allsw)//2], med([h for h, _ in csw])]
allh = sorted(h for h in ev['wave_height'] if h is not None)
R['hs_med'] = [allh[len(allh)//2], med([h for h, _ in cwv])]
print('total Hs median all hours %.2f, sighting-days %.2f' % tuple(R['hs_med']))
json.dump(R, open(os.path.join(CACHE, 'extras_result.json'), 'w'))
print('written')
