-- ============================================================================
-- analytics_active_daily — daily and rolling-7-day active devices
-- ============================================================================
-- Powers the "Active devices" chart in ?stats=1, which is the panel's most
-- trustworthy count.
--
-- WHY THIS EXISTS RATHER THAN BEING DERIVED CLIENT-SIDE
--   analytics_summary already returns devices-per-day, but distinct-devices-over-
--   7-days is NOT the sum of seven daily counts — a swimmer who comes Monday and
--   Thursday is one weekly device and two daily ones. Rolling WAU has to be
--   counted in the database, over the window, with a real distinct.
--
-- WHY IT IS THE NUMBER TO TRUST
--   The device id lives only in localStorage, which Safari evicts after 7 days of
--   non-use, so one swimmer can return as a new id. Splitting requires 7 idle
--   days — which means it cannot happen inside a single day, and inside a 7-day
--   window it would have to consume the entire window. So DAU is effectively
--   immune to that churn and WAU is near enough. The cumulative device total, by
--   contrast, keeps every re-birth forever.
--
-- Read-only. Admin-gated via intro_is_admin, same as the other analytics RPCs.
--
-- HOW TO RUN
--   1. Paste this whole file into the Supabase SQL editor and Run.
--      "Success. No rows returned" means the function was installed. That is the
--      create step — it is not the result, and it has to happen before step 2 or
--      you get "function ... does not exist".
--   2. Check it, with the same token you use for ?stats=1&token=… :
--
--        select * from public.analytics_active_daily('YOUR_ADMIN_TOKEN', 30);
--
--      NOTE the plain select. This function returns a TABLE of rows, unlike
--      analytics_device_quality and analytics_device_gaps which return jsonb —
--      wrapping this one in jsonb_pretty() fails on a type mismatch.
--      Expect one row per day: day | dau | wau.
--
--   The stats panel picks the function up on the next open; until then the
--   "Active devices" card shows a prompt to run this file.
-- ============================================================================

create or replace function public.analytics_active_daily(p_token text, p_days int default 60)
returns table(day date, dau int, wau int)
language plpgsql
security definer
set search_path = public
as $function$
begin
  if not coalesce(intro_is_admin(p_token), false) then
    raise exception 'not authorised';
  end if;

  return query
  with e as (                                   -- one row per device per local day
    select distinct
           ae.device_id,
           (ae.created_at at time zone 'Australia/Sydney')::date as d
    from analytics_events ae
    -- Reach back an extra 7 days so the earliest day's rolling window is complete
    -- rather than silently short.
    where ae.created_at >= now() - ((p_days + 7) * interval '1 day')
  ),
  days as (
    select generate_series(
             (now() at time zone 'Australia/Sydney')::date - (p_days - 1),
             (now() at time zone 'Australia/Sydney')::date,
             interval '1 day'
           )::date as day
  )
  select days.day,
         (select count(distinct e.device_id)::int
            from e where e.d = days.day)                                  as dau,
         (select count(distinct e.device_id)::int
            from e where e.d between days.day - 6 and days.day)           as wau
  from days
  order by days.day;
end
$function$;

grant execute on function public.analytics_active_daily(text, int) to anon;
