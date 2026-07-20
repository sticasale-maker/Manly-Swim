# Bluebottle forecast band — model & provenance

Cabbage Tree Bay / South Steyne, Manly. `BBF_` system in `index.html`. Last updated 2026-07.

A short, honest record of where the numbers come from, so the model's justification lives in the
repo rather than in a chat log. This band is a wind-driven forecast and is entirely separate from
the community photo-report system (`BB_`/Supabase).

## 1. What it does

Bluebottles (*Physalia* spp.) are wind-drifted surface sailors, so their arrival on a beach is
governed by onshore wind, not by waves or water temperature. The card:

1. projects the forecast wind onto the bay's shoreline orientation (`BBF_ASPECT = 82°`, ENE),
   keeping only the onshore component (`max(0, cos(windFrom − 82°)) × speed`);
2. averages that onshore component over the 3 hours up to the selected time;
3. maps the result to a four-band scale (Low / Building / Elevated / High) via thresholds
   `[2, 6, 12] km/h`;
4. caps the band in the cooler months, when few bluebottles are offshore to arrive.

It reports **relative risk** (how a day compares with an average day), not an absolute probability.

### Display: 6-hour windows (not part of the fit)

Steps 1–4 are the calibrated per-hour model. The **card** does not show a single hour — stamping a
band with a clock time would imply a resolution the model doesn't have. Instead it reports one band
per fixed 6-hour window (overnight / morning / afternoon / evening). For a window it takes the
per-hour onshore values inside the block and forms

```
window value = 0.6 · peak  +  0.4 · mean            (BBF_PEAK_WEIGHT = 0.6)
```

then bands that value through the same thresholds and seasonal cap. The **mean** is the integral of
the onshore curve over the window divided by its length (every window is a fixed 6 h, so mean ∝
integral); the **peak** is its strongest hour. Blending the two distinguishes a lone gust from a
sustained blow — a brief spike lifts the reading a little, a six-hour push lifts it a lot — and
leans toward the peak because this is a hazard cue. This blend is a **display heuristic**: the
0.6/0.4 weighting is chosen, not fitted, and it changes no threshold, multiplier, or cap. The dot
sits continuously within the winning band to show where the whole window falls, which is a
window-level quantity, not a per-hour one.

## 2. Source data

- **Sightings:** iNaturalist, genus *Physalia*, queried by taxon within a Sydney bounding box
  (Cronulla → Palm Beach). Not the hand-curated project #115085 (only ~400 manually added); the
  full taxon query returned 718 records, **705** after removing location-obscured
  (`geoprivacy = obscured`, fuzzed to ~20 km). File: [`data/obs_sydney.csv`](data/obs_sydney.csv),
  committed alongside this doc. Columns: `id, lat, lng, geoprivacy, observed_on,
  time_observed_at, place_guess`. Six records carry no usable `observed_on` and drop out of every
  date-based figure below. Observation records are from iNaturalist contributors — see
  <https://www.inaturalist.org> for per-record attribution and licence terms.

  Run [`verify_bluebottle_data.js`](verify_bluebottle_data.js) to reproduce the counts in this
  section straight from the CSV. As of 2026-07 all four data claims (705 / all-open / 556 / 443)
  reproduce exactly.
- **Cabbage Tree Bay subset:** 48 records (36 distinct sighting-days), 2011–2026.
- **Wind:** Open-Meteo — ERA5 reanalysis archive, cross-checked against the higher-resolution
  Historical Forecast model (~2–11 km). The two gave identical skill: bluebottle drift is
  synoptic-scale, so wind-field resolution doesn't change the result. `wx_cache/`.
- **Modelled span:** 2021–2026, where hourly wind is available (556 records → 443 stranding-days
  after collapsing same-day/same-beach reports).

## 3. Method

A **matched case–control** design: each stranding-day is compared with random typical days at the
same beach cluster (3 controls per case). This controls for observation effort (where people look)
and isolates the temporal wind signal. Because iNaturalist is presence-only, the design yields
**relative risk, not calibrated probability** — the honest fix for absolute probability is
"none-seen" data, which the app's community reports can supply over time. Skill is measured by AUC
under **year-blocked cross-validation** (test folds held out by year, so temporally autocorrelated
events can't leak).

## 4. How each parameter was set

- **Lag window (3 h):** chosen by sweeping `[1, 2, 3, 6, 9, 12, 24, 48] h`. Single-feature AUC
  peaks in the 1–6 h band (3 h nominally sharpest) and collapses beyond ~9 h — consistent with a
  recent-push mechanism. 3 h was selected as the responsive, near-optimal window.
- **Aspect (82°):** set from the bay's orientation (ENE open exposure), cross-checked against the
  site's own fetch geometry (`CHOP_FETCH_M`, exposure peak NNE) and validated empirically — a
  fetch-by-direction weighting was tested against the plain 82° cosine and lost (0.84 vs 0.89), so
  the simple cosine at 82° is retained. Tuning knob.
- **Thresholds (2 / 6 / 12 km/h):** round numbers placed near the empirical quartiles of prior-3 h
  onshore wind on stranding-days (25/50/75th ≈ 1.4 / 7.8 / 13.4 km/h), then sanity-checked to give
  a monotonic, well-separated risk gradient.
- **Multipliers (0.4 / 1.3 / 2.3 / 3.3×):** each band's stranding-day share divided by the overall
  stranding-day share in the matched set — i.e. relative risk versus a typical day at the same
  beach, over 2021–2026. Because the matched set is 1 : 3.7 case:control (not the true base rate),
  these are **relative, not absolute** — hence "× a typical day," never "%".
- **Seasonal cap:** derived from monthly distinct-stranding-day counts (the abundance envelope).
  Cap = High for Oct–Mar, Elevated for Apr/Sep, Building for May–Aug. The shape matches the
  peer-reviewed year-round lifeguard record (Bourg et al. 2022: ~50% of beachings in summer,
  near-zero in winter), so it is not an observation-effort artifact; the exact winter cut-offs are
  approximate.

  > **The stated cut-offs don't reproduce the shipped cap.** This doc originally gave the rule as
  > High ≥ 0.6, Elevated 0.3–0.6, Building < 0.3 on a max-normalised factor. Run against
  > `data/obs_sydney.csv` that rule yields `[3,2,3,1,1,1,1,1,1,3,2,2]`, not the shipped
  > `[3,3,3,2,1,1,1,1,2,3,3,3]` — Feb, Apr, Sep, Nov and Dec all come out one band lower.
  >
  > The likely cause is the normaliser: October carries 107 distinct stranding-days against
  > January's 68, so dividing by the maximum lets one exceptional month depress every other
  > month's factor. A mean- or median-normalised envelope, or a peak-season grouping done by eye
  > against the literature, all give something closer to the shipped values.
  >
  > **The shipped cap is retained**, for two reasons: it matches the Oct–Mar summer dominance in
  > Bourg et al., and in all five disagreeing months it is the *more* cautious of the two — it
  > warns where the arithmetic rule would not. The threshold arithmetic is what's unreliable here,
  > not the cap. Recorded rather than quietly corrected, because the next person to retune this
  > will otherwise rediscover the same gap. `verify_bluebottle_data.js` prints the comparison.

## 5. Validation

- **Region model (onshore wind):** AUC ≈ 0.82 (wind alone), 0.86 with per-beach aspect and the lag
  sweep. Adding waves, sea temperature, wind-field resolution, per-beach fetch weighting, and an
  explicit two-stage (E-then-N) drift term each produced **no improvement** — the simple onshore
  band is the parsimonious best.
- **Cabbage Tree Bay:** prior-3 h onshore separates strandings from typical days with
  **AUC 0.89 (90% CI 0.82–0.95)** on the 48 local records.
- **Independent corroboration:** onshore wind is the primary driver of *Physalia* beachings at both
  daily and seasonal scales in the peer-reviewed literature (Bourg et al. 2022; Hewitt, Schaeffer
  et al. 2025).

## 6. Limitations

- **Relative, not absolute.** Even "High" is an elevated likelihood, not a certainty; the most
  stranding-prone days are far from guaranteed. There is deliberately no "100%".
- **Winter cut-offs approximate** (shape solid, magnitude to be refined with local reports).
- **Bay sample is small** (48 sightings) — a rare wind-decoupled event could sit below detection.
- **Surface currents unmodelled.** The East Australian Current and local eddies add non-linearity
  wind alone can't capture; this is a wind-drift model, not full transport.
- **Rip currents** (a known beach-scale predictor) can't be fed — no forecast product exists.

## 7. References

- Bourg N., Schaeffer A., et al. (2022). *Driving the blue fleet: temporal variability and drivers
  behind bluebottle beaching.* PLoS ONE 17(3): e0265593.
  [doi:10.1371/journal.pone.0265593](https://doi.org/10.1371/journal.pone.0265593)
- Hewitt, Schaeffer, et al. (2025). *Blowin' in the wind: onshore winds drive the occurrence of
  bluebottles (Physalia spp.) at east Australian beaches.* Ocean & Coastal Management.
- iNaturalist (genus *Physalia* observations, Sydney). <https://www.inaturalist.org>
- Open-Meteo ERA5 reanalysis & Historical Forecast API. <https://open-meteo.com>
