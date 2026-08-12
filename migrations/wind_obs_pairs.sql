-- ============================================================================
-- wind_obs_pairs — observed wind (BOM North Head) vs forecast wind, same instant
-- ============================================================================
-- STEP 1 of the real-time-wind plan (12 Aug 2026). This table does NOT change any
-- score. It exists to answer two questions before nudging is switched on:
--
--   1. What is the STATION BIAS?  North Head is an ~85 m clifftop; the forecast is
--      10 m wind for the swim patch. So obs/forecast has a large PERSISTENT ratio
--      that is terrain, not forecast error. Nudging on the raw ratio would bake
--      that inflation into every score. We need k = median(fc_kmh / obs_kmh).
--   2. How fast does a forecast error DECAY? That sets tau in the decaying
--      bias correction. Answerable later by joining rows on lead time.
--
-- NOT folded into calibration_pairs on purpose. That table is keyed on the
-- DISPLAYED forecast hour (user-selected, one row per clock-hour) and its uniq
-- constraint is (forecast_ts, device_id). This one is keyed on the BOM OBSERVATION
-- instant (half-hourly, never user-selected). Same key would collide; different
-- key in the same table would break the existing hourly throttle.
--
-- HOW TO RUN: paste into the Supabase SQL editor and Run. Purely additive.
-- ============================================================================

create table if not exists public.wind_obs_pairs (
  id           bigint generated always as identity primary key,
  obs_ts       timestamptz not null,        -- BOM aifstime_utc of the LATEST reading averaged
  logged_at    timestamptz not null default now(),
  device_id    text        not null,
  app_build    text,

  -- Observation side (vector-averaged over the trailing window, see index.html)
  station      text        not null,        -- 'northhead' | 'sydneyharbour' | 'fortdenison'
  obs_kmh      numeric,                     -- scalar mean speed over the window
  obs_gust_kmh numeric,                     -- max gust in the window
  obs_dir_deg  numeric,                     -- vector-mean direction, degrees FROM
  obs_n        int,                         -- how many readings went into the mean
  obs_age_min  numeric,                     -- age of the newest reading when logged
  -- |vector mean| / scalar mean, 0..1. 1 = dead steady, low = the wind swung
  -- across the window so obs_dir_deg is not meaningful. A direction nudge should
  -- later be gated on this; logging it now so the gate can be calibrated.
  obs_steadiness numeric,

  -- Forecast side, INTERPOLATED to obs_ts (not the nominal hour — comparing a
  -- 10-min mean at :30 against an hourly value at :00 injects phase as bias)
  fc_kmh       numeric,
  fc_dir_deg   numeric,
  fc_src       text,                        -- 'ww' — which feed supplied the wind
  fc_row_ts    text,                        -- the matrix row the interpolation bracketed

  -- Derived, stored so a query does not have to re-derive them consistently
  ratio        numeric,                     -- obs_kmh / fc_kmh  (BEFORE any k correction)
  dir_delta_deg numeric                     -- shortest-arc (obs_dir - fc_dir), -180..180
);

-- One row per observation instant per device.
--
-- NOTE the client does a PLAIN INSERT against this, not the on_conflict upsert
-- logCalibrationPair uses. PostgREST routes an upsert through its UPDATE path, so
-- an upsert needs an UPDATE policy too — and granting UPDATE on an append-only
-- measurement table would let any client PATCH rows. Instead the client lets this
-- index return 409 and treats that as "already recorded". Do NOT add an UPDATE
-- policy here to "fix" a 409; the 409 is the design.
create unique index if not exists wind_obs_pairs_uniq
  on public.wind_obs_pairs (obs_ts, device_id);

create index if not exists wind_obs_pairs_ts
  on public.wind_obs_pairs (obs_ts desc);

alter table public.wind_obs_pairs enable row level security;

-- Insert-only, matching how calibration_pairs is written (direct PostgREST POST
-- with the publishable key). No select policy: nothing in the app reads this back;
-- analysis happens in the SQL editor as the service role, which bypasses RLS.
--
-- `to public`, NOT `to anon` (corrected 13 Aug 2026). The first version restricted
-- the policy to the anon role and every insert came back 42501 "new row violates
-- row-level security policy", while the identical POST to calibration_pairs
-- succeeded. A missing GRANT reports "permission denied for table", so the GRANT
-- was never the problem — the role the sb_publishable_* key resolves to simply did
-- not match the policy's role list. `to public` matches whatever role the request
-- arrives as; the with-check is still the thing that authorises the row, and the
-- GRANT below is still what stops any other role writing here.
drop policy if exists wind_obs_pairs_insert_anon on public.wind_obs_pairs;
create policy wind_obs_pairs_insert_anon
  on public.wind_obs_pairs
  for insert
  to public
  with check (true);

grant insert on public.wind_obs_pairs to anon, authenticated;
-- Identity columns own their sequence implicitly, so no sequence grant is needed
-- (the earlier `grant ... on all sequences` here was noise and is removed).

-- Confirm the policy actually landed — run this and expect exactly one row:
--   select policyname, roles, cmd from pg_policies
--   where schemaname = 'public' and tablename = 'wind_obs_pairs';

-- ============================================================================
-- ANALYSIS — run these after a few weeks of collection.
-- ============================================================================
--
-- 1) The station bias k. Use the MEDIAN, not the mean: the ratio is a ratio of
--    positive quantities and its distribution has a long right tail (a light-wind
--    hour where fc = 2 km/h produces an enormous ratio that a mean would follow).
--    Restrict to decent wind for the same reason.
--
--   select percentile_cont(0.5) within group (order by fc_kmh / obs_kmh) as k,
--          count(*) as n
--   from wind_obs_pairs
--   where obs_kmh >= 8 and fc_kmh >= 8 and obs_steadiness >= 0.8;
--
-- 2) Is the bias direction-dependent? Almost certainly yes — a clifftop over-reads
--    most in the onshore sector and the lee arc behaves differently. If the spread
--    across sectors is large, k has to become a curve, not a scalar.
--
--   select width_bucket(obs_dir_deg, 0, 360, 8) as sector,
--          round(avg(obs_dir_deg))              as mid_deg,
--          percentile_cont(0.5) within group (order by fc_kmh / obs_kmh) as k,
--          count(*) as n
--   from wind_obs_pairs
--   where obs_kmh >= 8 and fc_kmh >= 8
--   group by 1 order by 1;
--
-- 3) Direction offset — is WW systematically rotated at this site?
--
--   select percentile_cont(0.5) within group (order by dir_delta_deg) as median_delta,
--          count(*) as n
--   from wind_obs_pairs
--   where obs_kmh >= 10 and obs_steadiness >= 0.9;
-- ============================================================================
