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
-- Read-only. Admin-gated the same way as analytics_summary (intro_is_admin).
-- Additive CREATE OR REPLACE — it cannot affect existing analytics.
--
-- HOW TO RUN: paste into the Supabase SQL editor and Run. Then call it the same
-- way the dashboard calls its other RPCs, with your admin token.
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
           -- common path, so `bool_or` and not the value on the first event.
           bool_or(coalesce(ae.pwa, false))                as ever_pwa
    from analytics_events ae
    where ae.created_at >= now() - (p_days * interval '1 day')
    group by ae.device_id
  ),
  b as (
    select d.*,
           -- Lifespan in whole days, Sydney local, so "same day" means one visit
           -- session rather than a clock artefact around UTC midnight.
           ((d.last_seen at time zone 'Australia/Sydney')::date
          - (d.first_seen at time zone 'Australia/Sydney')::date) as life_days
    from d
  )
  select jsonb_build_object(
    'devices',        (select count(*) from b),
    'sessions',       (select coalesce(sum(sessions), 0) from b),

    -- Headline: share of devices that were never seen on a second day. Compare
    -- this between the two groups below, not in isolation.
    'oneday_share',   (select round(100.0 * count(*) filter (where life_days = 0)
                                    / greatest(count(*), 1), 1) from b),

    -- The comparison that matters. Installed devices keep their storage; browser
    -- devices lose it after 7 idle days. Same people, different durability.
    'by_install',     (select jsonb_agg(x order by x->>'group')
                       from (
                         select jsonb_build_object(
                           'group',         case when ever_pwa then 'installed (pwa)' else 'browser' end,
                           'devices',       count(*),
                           'oneday_share',  round(100.0 * count(*) filter (where life_days = 0)
                                                  / greatest(count(*), 1), 1),
                           -- Devices last seen 8+ days after first contact cannot be
                           -- an evicted-and-reborn id: they survived the 7-day window.
                           'survived_7d',   round(100.0 * count(*) filter (where life_days >= 8)
                                                  / greatest(count(*), 1), 1),
                           'median_life_days', percentile_cont(0.5) within group (order by life_days),
                           'median_sessions',  percentile_cont(0.5) within group (order by sessions)
                         ) as x
                         from b group by ever_pwa
                       ) s),

    -- Lifespan histogram, for the shape of the tail.
    'lifespan',       (select jsonb_agg(x order by x->>'bucket')
                       from (
                         select jsonb_build_object(
                           'bucket', case when life_days = 0        then 'a: same day only'
                                          when life_days between 1 and 6  then 'b: 1-6 days'
                                          when life_days = 7        then 'c: exactly 7 days'
                                          when life_days between 8 and 29 then 'd: 8-29 days'
                                          else 'e: 30+ days' end,
                           'devices', count(*)
                         ) as x
                         from b
                         group by 1
                       ) s),

    -- Sessions-per-device: an evicted id starts a fresh life, so churn pushes mass
    -- toward 1. A genuine one-time visitor also sits at 1 — which is exactly why
    -- this is read alongside by_install, never on its own.
    'single_session_share', (select round(100.0 * count(*) filter (where sessions = 1)
                                          / greatest(count(*), 1), 1) from b)
  ) into result;

  return result;
end
$function$;

grant execute on function public.analytics_device_quality(text, int) to anon;
