-- ============================================================================
-- analytics_install_trend — installed (pwa) vs browser-only active devices, by day
-- ============================================================================
-- Companion to analytics_device_quality: that function gives the current
-- installed/browser split as one snapshot, this gives its trend over time —
-- how many distinct devices were ACTIVE on each day, split by whether that
-- device has EVER run as an installed PWA (its whole history, not just the
-- window), same "ever_pwa" definition as analytics_device_quality.
--
-- Read-only, additive. Admin-gated the same way as the other analytics RPCs.
--
-- HOW TO RUN
--   1. Paste this whole file into the Supabase SQL editor and Run.
--   2. select jsonb_pretty(public.analytics_install_trend('YOUR_ADMIN_TOKEN', 90));
-- ============================================================================

create or replace function public.analytics_install_trend(p_token text, p_days int default 90)
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

  with ever as (                                -- installed status is a device's whole history
    select device_id, bool_or(coalesce(pwa, false)) as ever_pwa
    from analytics_events
    group by device_id
  ),
  daily as (                                    -- one row per device active that day
    select (ae.created_at at time zone 'Australia/Sydney')::date as day,
           ae.device_id
    from analytics_events ae
    where ae.created_at >= now() - (p_days * interval '1 day')
    group by 1, 2
  ),
  agg as (
    select d.day,
           count(*) filter (where e.ever_pwa)     as installed,
           count(*) filter (where not e.ever_pwa) as browser
    from daily d
    join ever e using (device_id)
    group by d.day
  )
  select coalesce(jsonb_agg(to_jsonb(agg) order by agg.day), '[]'::jsonb) into result from agg;

  return result;
end
$function$;

grant execute on function public.analytics_install_trend(text, int) to anon;
