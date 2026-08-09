-- ============================================================================
-- analytics_device_quality — how much is the device count inflated by id churn?
-- ============================================================================
-- The dashboard counts distinct device_id. That id lives ONLY in localStorage
-- (index.html deviceId(), ~line 3488), which Safari's ITP evicts after 7 days of
-- non-use on a non-installed web app — so one returning swimmer can arrive as a
-- second, third, fourth "device", each one also counted as new_devices and so
-- also added to the cumulative total.
--
-- This function does NOT identify anyone or add any collection. It only asks a
-- shape question of rows that already exist: how long does a device_id live, and
-- does that lifespan differ between installed (pwa) and browser sessions?
--
-- HOW TO READ THE RESULT
--   The eviction signature is: a large `oneday_share` among NON-pwa devices,
--   while pwa devices show long lifespans. Installed apps are exempt from the
--   7-day eviction, so they are the control group — they are the same kind of
--   human, on the same kind of phone, with durable storage. If browser devices
--   die at a week and installed ones don't, the gap is the artefact, not a
--   difference in how people swim.
--
--   If instead both groups look alike, the count is honest and the one-day
--   devices are simply people who looked once and never came back.
--
--   Also watch `lifespan` for a pile-up in the "exactly 7 days" bucket. That is
--   the eviction fingerprint and it is hard to produce any other way.
--
-- Read-only. Admin-gated the same way as analytics_summary (intro_is_admin).
-- Additive CREATE OR REPLACE — it cannot affect existing analytics.
--
-- HOW TO RUN
--   1. Paste this whole file into the Supabase SQL editor and Run. Creating a
--      function reports "Success. No rows returned" — that is the function being
--      installed, not an empty result.
--   2. Then CALL it, with the same token you use for ?stats=1&token=… :
--        select jsonb_pretty(public.analytics_device_quality('YOUR_ADMIN_TOKEN', 400));
--
-- NOTE: every grouped total is built in a plain CTE and only turned into JSON
-- afterwards. Building the object inside the grouped query means `group by 1`
-- lands on an expression containing count(*), which Postgres rejects with
-- "aggregate functions are not allowed in GROUP BY".
-- ============================================================================

create or replace function public.analytics_device_quality(p_token text, p_days int default 400)
returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  result jsonb;
begin
  if not coalesce(intro_is_admin(p_token), false) then
    raise exception 'not authorised';
  end if;

  with d as (                                  -- one row per device_id
    select ae.device_id,
           min(ae.created_at)                              as first_seen,
           max(ae.created_at)                              as last_seen,
           count(distinct ae.session_id)                   as sessions,
           -- "ever installed": a device counts as PWA if ANY of its events came
           -- from the standalone display mode. Installing part-way through is the
           -- common path, so bool_or and not the value on the first event.
           bool_or(coalesce(ae.pwa, false))                as ever_pwa
    from analytics_events ae
    where ae.created_at >= now() - (p_days * interval '1 day')
    group by ae.device_id
  ),
  b as (
    select d.device_id,
           d.sessions,
           d.ever_pwa,
           -- Lifespan in whole days, Sydney local, so "same day" means one visit
           -- rather than a clock artefact around UTC midnight.
           ((d.last_seen  at time zone 'Australia/Sydney')::date
          - (d.first_seen at time zone 'Australia/Sydney')::date) as life_days
    from d
  ),
  -- The comparison that matters. Installed devices keep their storage; browser
  -- devices lose it after 7 idle days. Same people, different durability.
  grp as (
    select case when b.ever_pwa then 'installed (pwa)' else 'browser' end as group_name,
           count(*)::int                                                  as devices,
           round(100.0 * count(*) filter (where b.life_days = 0)
                 / greatest(count(*), 1), 1)                              as oneday_share,
           -- Devices last seen 8+ days after first contact cannot be an
           -- evicted-and-reborn id: they survived the 7-day window.
           round(100.0 * count(*) filter (where b.life_days >= 8)
                 / greatest(count(*), 1), 1)                              as survived_7d,
           round((percentile_cont(0.5) within group (order by b.life_days))::numeric, 1) as median_life_days,
           round((percentile_cont(0.5) within group (order by b.sessions))::numeric, 1)  as median_sessions
    from b
    group by 1                                  -- position 1 is the CASE, not an aggregate
  ),
  life as (
    select case when b.life_days = 0              then 'a: same day only'
                when b.life_days between 1 and 6  then 'b: 1-6 days'
                when b.life_days = 7              then 'c: exactly 7 days'
                when b.life_days between 8 and 29 then 'd: 8-29 days'
                else                                   'e: 30+ days' end as bucket,
           count(*)::int                                                 as devices
    from b
    group by 1                                  -- same: grouping on the CASE
  )
  select jsonb_build_object(
    'devices',              (select count(*) from b),
    'sessions',             (select coalesce(sum(b.sessions), 0) from b),

    -- Headline: share of devices never seen on a second day. Compare it between
    -- the two groups below, not in isolation.
    'oneday_share',         (select round(100.0 * count(*) filter (where b.life_days = 0)
                                          / greatest(count(*), 1), 1) from b),

    'by_install',           (select jsonb_agg(to_jsonb(grp) order by grp.group_name) from grp),
    'lifespan',             (select jsonb_agg(to_jsonb(life) order by life.bucket) from life),

    -- Sessions-per-device: an evicted id starts a fresh life, so churn pushes mass
    -- toward 1. A genuine one-time visitor also sits at 1 — which is exactly why
    -- this is read alongside by_install, never on its own.
    'single_session_share', (select round(100.0 * count(*) filter (where b.sessions = 1)
                                          / greatest(count(*), 1), 1) from b)
  )
  into result;

  return result;
end
$function$;

grant execute on function public.analytics_device_quality(text, int) to anon;
