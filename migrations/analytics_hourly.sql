-- ============================================================================
-- analytics_hourly — accesses (sessions) per hour, per day, for the stats panel
-- ============================================================================
-- Powers the "Accesses by hour — last 7 days" line chart in ?stats=1.
-- An "access" = one session (distinct session_id), counted at the hour its FIRST
-- event fired, in Australia/Sydney local time. Admin-gated the same way as
-- analytics_summary (intro_is_admin). This is a single additive CREATE OR REPLACE
-- — it does not drop or alter anything, so it can't break existing analytics.
--
-- HOW TO RUN: paste into the Supabase SQL editor and Run.
--
-- If calling it errors with 'function intro_is_admin(...) does not exist', tell me
-- the name of your analytics admin-check function and I'll swap the guard.
-- ============================================================================

create or replace function public.analytics_hourly(p_token text, p_days int default 7)
returns table(day date, hour int, accesses int)
language plpgsql
security definer
set search_path = public
as $function$
begin
  if not coalesce(intro_is_admin(p_token), false) then
    raise exception 'not authorised';
  end if;

  return query
  with s as (                                    -- each session at its first event
    select ae.session_id, min(ae.created_at) as t
    from analytics_events ae
    where ae.created_at >= now() - ((p_days + 1) * interval '1 day')
    group by ae.session_id
  )
  select (s.t at time zone 'Australia/Sydney')::date                       as day,
         extract(hour from (s.t at time zone 'Australia/Sydney'))::int     as hour,
         count(*)::int                                                     as accesses
  from s
  where (s.t at time zone 'Australia/Sydney')::date
        >= (now() at time zone 'Australia/Sydney')::date - (p_days - 1)
  group by 1, 2
  order by 1, 2;
end
$function$;

grant execute on function public.analytics_hourly(text, int) to anon;
