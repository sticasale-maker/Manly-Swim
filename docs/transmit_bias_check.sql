-- ============================================================================
-- Is the wind-wave transmit being applied twice actually hurting Entry?
--
-- BACKGROUND. calcScore's NS branch does:
--     ww_d      = H^2 * P * nsDispLookup(wwDir, wwP) * ss_wave_sens
--     windEntry = ws_d * (1 - w) + ww_d * w        where w == nsDispLookup(...)
-- so the NS sea partition reaches Entry as H^2*P*transmit^2, i.e. the shelter is
-- applied twice. The dispersion table says 14% gets in from the SE; the model
-- behaves as though 2% does. Confirmed numerically 24 Aug 2026 (solved w =
-- 0.8429 vs wwTx 0.8426 at 07:00, 0.9076 vs 0.9075 at 08:00).
--
-- It only affects the SEA partition. swell1 is added straight into `o` with the
-- transmit applied once and is unaffected.
--
-- PREDICTION IF IT IS WRONG. The model should be too CALM specifically when the
-- sea partition arrives on a poorly-transmitting bearing, because that is where
-- squaring bites (x1.01 at due N, x7 at 135 deg). Swimmers should be putting the
-- Entry dot to the RIGHT of the model's dot on those hours and nowhere else.
-- A bias present at ALL bearings is a sensitivity problem (ss_wave_sens), NOT
-- this. That separation is the whole point of bucketing by bearing.
--
-- WHICH TABLE. calibration_pairs holds NO human judgement -- it pairs three
-- MODEL variants against each other, so it cannot answer "is the model wrong".
-- Query 2 (the actual test) therefore runs on calibration_captures, which is
-- what the "My call" rails write. calibration_pairs is used in Query 3 only to
-- size how often the low-transmit case even arises.
--
-- Rails are drawn best-on-left, so a HIGHER pct means rougher, and
--     residual = assess_entry_pct - auto_entry_pct
-- is POSITIVE when the swimmer says it is rougher than the model does.
--
-- TRANSMIT BANDS. nsDispLookup at P=6s, sampled 24 Aug 2026. Symmetric about
-- the N-S axis: ~1.00 at 0 deg, 0.89 at 60, 0.54 at 90, 0.25 at 120, 0.14 at 135.
--     high  tx >= 0.85   dir <= 60  or dir >= 300     squaring costs <= 15%
--     mid   0.5-0.85     60-90      or 270-300        squaring costs 15-45%
--     low   tx < 0.5     90-270                       squaring costs > 45%
-- ============================================================================


-- ── 1. DO WE EVEN HAVE THE DATA? Run this first. ────────────────────────────
-- "My call" needs three sliders set and a deliberate freeze, so n is likely
-- small. If low_transmit_n is under ~15 the rest of this file cannot conclude
-- anything and the honest answer is "collect more, or decide on physics".
SELECT
  count(*)                                                    AS captures_total,
  count(*) FILTER (WHERE assess_entry_pct IS NOT NULL
                     AND auto_entry_pct  IS NOT NULL)         AS usable,
  count(DISTINCT device_id)                                   AS devices,
  min(captured_at)::date                                      AS first_capture,
  max(captured_at)::date                                      AS last_capture,
  count(*) FILTER (WHERE windwave_dir > 90 AND windwave_dir < 270
                     AND windwave_h > 0.15)                   AS low_transmit_n
FROM calibration_captures
WHERE model = 'gem';          -- the live NS-dispersion path only


-- ── 2. THE TEST. Entry residual by transmit band. ───────────────────────────
-- Read it like this:
--   * residual rises monotonically high -> mid -> low   => transmit is being
--     applied twice; the fix is real and worth the re-tune.
--   * residual roughly FLAT across bands, but non-zero  => a level problem;
--     re-fit ss_wave_sens instead and leave the crossfade alone.
--   * residual noisy / CI straddles zero everywhere     => not enough evidence.
--     Do NOT re-shape the direction response on this.
WITH c AS (
  SELECT
    CASE
      WHEN windwave_dir <=  60 OR windwave_dir >= 300 THEN 'high  (tx>=0.85)'
      WHEN windwave_dir <=  90 OR windwave_dir >= 270 THEN 'mid   (tx 0.5-0.85)'
      ELSE                                                 'low   (tx<0.5)'
    END                                        AS transmit_band,
    assess_entry_pct - auto_entry_pct          AS residual,
    d_windwave,
    d_swell1,
    -- how much of this hour's Entry is the sea partition at all. Where the sea
    -- is a small share, this bug cannot be what moved the dot, so it is a
    -- useful sanity column rather than a filter.
    CASE WHEN (COALESCE(d_windwave,0) + COALESCE(d_swell1,0)) > 0
         THEN COALESCE(d_windwave,0) / (COALESCE(d_windwave,0) + COALESCE(d_swell1,0))
    END                                        AS sea_share
  FROM calibration_captures
  WHERE model = 'gem'
    AND assess_entry_pct IS NOT NULL
    AND auto_entry_pct   IS NOT NULL
    AND windwave_dir     IS NOT NULL
    AND windwave_h       > 0.15      -- a sea worth attributing anything to
)
SELECT
  transmit_band,
  count(*)                                              AS n,
  round(avg(residual)::numeric, 1)                      AS mean_residual_pct,
  round(stddev_samp(residual)::numeric, 1)              AS sd,
  -- normal-approx 95% CI on the mean. Crude, but enough to see whether the
  -- band is distinguishable from zero at the sample sizes involved here.
  round((avg(residual) - 1.96 * stddev_samp(residual) / sqrt(count(*)))::numeric, 1) AS ci95_lo,
  round((avg(residual) + 1.96 * stddev_samp(residual) / sqrt(count(*)))::numeric, 1) AS ci95_hi,
  round(avg(sea_share)::numeric, 2)                     AS mean_sea_share,
  count(*) FILTER (WHERE residual > 0)                  AS n_model_too_calm,
  count(*) FILTER (WHERE residual < 0)                  AS n_model_too_rough
FROM c
GROUP BY transmit_band
ORDER BY transmit_band;        -- high, low, mid alphabetically; read the labels


-- ── 2b. Same thing unbucketed, so a real trend is not a bucket-edge artifact.
-- The banding above is my choice, not the data's. If the effect is real it
-- should also show as a downward slope in mean_residual against bearing
-- distance from North, without any CASE statement helping it along.
SELECT
  width_bucket(
    least(abs(windwave_dir - 0), 360 - abs(windwave_dir - 0)),  -- degrees off North
    0, 180, 6
  ) * 30 - 15                                           AS deg_off_north_bucket,
  count(*)                                              AS n,
  round(avg(assess_entry_pct - auto_entry_pct)::numeric, 1) AS mean_residual_pct
FROM calibration_captures
WHERE model = 'gem'
  AND assess_entry_pct IS NOT NULL
  AND auto_entry_pct   IS NOT NULL
  AND windwave_dir     IS NOT NULL
  AND windwave_h       > 0.15
GROUP BY 1
HAVING count(*) >= 3
ORDER BY 1;


-- ── 3. EXPOSURE. How often does the low-transmit case even happen? ──────────
-- Runs on calibration_pairs because it is logged automatically every hour, so
-- it has far more rows than the hand-captured table and is the right source for
-- "how much of the year does this touch". No human judgement needed or used.
--
-- If low+mid is a small share of hours AND those hours carry little Entry, the
-- fix is correct-but-cosmetic and can wait for the next re-tune. If they are a
-- meaningful share, it should be done deliberately with its own fit.
SELECT
  CASE
    WHEN ns_ww_dir <=  60 OR ns_ww_dir >= 300 THEN 'high  (tx>=0.85)'
    WHEN ns_ww_dir <=  90 OR ns_ww_dir >= 270 THEN 'mid   (tx 0.5-0.85)'
    ELSE                                           'low   (tx<0.5)'
  END                                                   AS transmit_band,
  count(*)                                              AS hours,
  round(100.0 * count(*) / sum(count(*)) OVER (), 1)    AS pct_of_hours,
  round(avg(ns_entry_raw)::numeric, 1)                  AS mean_entry_raw,
  round(avg(ns_ww_h)::numeric, 2)                       AS mean_sea_h,
  round(avg(ns_ww_p)::numeric, 2)                       AS mean_sea_p,
  -- Entry hours that actually matter to a swimmer: at or past the Doable edge.
  count(*) FILTER (WHERE ns_entry_raw > 30)             AS hours_past_doable
FROM calibration_pairs
WHERE ns_ww_dir IS NOT NULL
  AND ns_ww_h   > 0.15
  AND ns_status = 'ok'          -- exclude rows scored on a degraded NS feed
GROUP BY 1
ORDER BY 1;


-- ── 4. OPTIONAL: the worst individual disagreements, for eyeballing. ────────
-- Small-n work is easier to trust when you can recognise the mornings. If the
-- top rows are all southerly seas, that is the effect; if they are scattered,
-- it is noise.
SELECT
  forecast_dt,
  round(windwave_dir::numeric, 0)                AS sea_dir,
  windwave_h                                     AS sea_h,
  windwave_p                                     AS sea_p,
  entry_label,
  auto_entry_pct,
  assess_entry_pct,
  assess_entry_pct - auto_entry_pct              AS residual,
  d_windwave,
  d_swell1
FROM calibration_captures
WHERE model = 'gem'
  AND assess_entry_pct IS NOT NULL
  AND auto_entry_pct   IS NOT NULL
  AND windwave_h       > 0.15
ORDER BY abs(assess_entry_pct - auto_entry_pct) DESC
LIMIT 25;
