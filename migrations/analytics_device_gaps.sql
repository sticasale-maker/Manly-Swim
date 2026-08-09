-- ============================================================================
-- analytics_device_gaps — does a device id survive a long idle gap?
-- ============================================================================
-- THE QUESTION THIS SETTLES
--   Safari's ITP evicts localStorage after 7 days of non-use on a non-installed
--   web app. The device id lives only there (index.html deviceId(), ~line 3488),
--   so an evicted id CANNOT come back: the same phone returns wearing a new id.
--
--   Therefore: a device id that spans an idle gap of more than 7 days is proof
--   that its storage was NOT evicted. Measure the longest gap between
--   consecutive sessions per device and the answer falls out directly.
--
--     browser ids almost never exceed 7 days  → eviction is real and the device
--                                               count is inflated by re-births
--     browser ids routinely exceed 7 days     → storage is surviving; the high
--                                               one-day share is genuine
--                                               one-time visitors, not churn
--
-- WHY NOT JUST COMPARE LIFESPANS (analytics_device_quality)
--   Because installing is an act of commitment: installed devices are keener
--   people, not merely better-stored ones. That comparison confounds durability
--   with enthusiasm. Idle-gap survival is a property of the STORAGE, so it
--   separates the two — a keen user and a casual one are both equally unable to
--   bridge a 7-day gap once their id has been wiped.
--
--   Caveat that remains: we deliberately do not collect user agents, so Safari
--   cannot be isolated from Chrome/Android (which does not evict this way). A
--   healthy share of long browser gaps may partly be non-Safari devices. Read
--   this as an upper bound on how durable browser ids are.
--
-- Read-only, adds no collection, identifies no one. Admin-gated via
-- intro_is_admin, same as the other analytics RPCs.
--
-- HOW TO RUN
--   1. Paste this whole file into the SQL editor and Run.
--      "Success. No rows returned" = the function was installed.
--   2. Then call it:
--        select jsonb_pretty(public.analytics_device_gaps('YOUR_ADMIN_TOKEN', 400));
-- ============================================================================

create or replace function public.analytics_device_gaps(p_token text, p_days int default 400)
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

  with ses as (                                   -- one row per session, at its first event
    select ae.device_id,
           ae.session_id,
           min(ae.created_at)                as t,
           bool_or(coalesce(ae.pwa, false))  as pwa
    from analytics_events ae
    where ae.created_at >= now() - (p_days * interval '1 day')
    group by ae.device_id, ae.session_id
  ),
  dev as (
    select ses.device_id,
           bool_or(ses.pwa)  as ever_pwa,
           count(*)::bigint  as sessions
    from ses
    group by ses.device_id
  ),
  g as (                                          -- gap from the previous session, in days
    select ses.device_id,
           extract(epoch from (ses.t - lag(ses.t) over (partition by ses.device_id order by ses.t)))
             / 86400.0 as gap_days
    from ses
  ),
  mx as (                                         -- only devices with 2+ sessions have a gap
    select g.device_id, max(g.gap_days) as max_gap
    from g
    where g.gap_days is not null
    group by g.device_id
  ),
  j as (
    select case when dev.ever_pwa then 'installed (pwa)' else 'browser' end as group_name,
           mx.max_gap
    from dev
    join mx on mx.device_id = dev.device_id
  ),
  -- THE HEADLINE. Among devices that came back at all, how many bridged a gap
  -- longer than the eviction window?
  agg as (
    select j.group_name,
           count(*)::int                                                        as returning_devices,
           round(100.0 * count(*) filter (where j.max_gap > 7)
                 / greatest(count(*), 1), 1)                                    as pct_gap_over_7d,
           round(100.0 * count(*) filter (where j.max_gap > 14)
                 / greatest(count(*), 1), 1)                                    as pct_gap_over_14d,
           round((percentile_cont(0.9) within group (order by j.max_gap))::numeric, 1) as p90_max_gap_days,
           round(max(j.max_gap)::numeric, 1)                                    as longest_gap_days
    from j
    group by 1                                    -- position 1 is group_name, a plain column
  ),
  -- Secondary, and worth knowing on its own: which group actually generates the
  -- usage. A small installed base can account for most of the sessions.
  usage as (
    select case when dev.ever_pwa then 'installed (pwa)' else 'browser' end as group_name,
           count(*)::int                                                    as devices,
           sum(dev.sessions)::bigint                                        as sessions
    from dev
    group by 1
  )
  select jsonb_build_object(
    'note',        'Gap stats cover devices with 2+ sessions only. An id that bridges a >7d idle gap was not evicted.',
    'by_install',  (select jsonb_agg(to_jsonb(agg)   order by agg.group_name)   from agg),
    'usage',       (select jsonb_agg(to_jsonb(usage) order by usage.group_name) from usage)
  )
  into result;

  return result;
end
$function$;

grant execute on function public.analytics_device_gaps(text, int) to anon;
