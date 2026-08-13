-- ============================================================================
-- Bag theft: take the aggregate summary away from the public
-- ============================================================================
-- Run this AFTER bag_theft_reports.sql.
--
-- WHY
--
-- The first migration let anyone call bag_theft_summary() to get counts, total
-- value, peak hours and a coarse hotspot grid, and the app showed a digest of it
-- on the strip. Marco wants to see the data first and decide what the community
-- is told and how — which is the right order. A raw count posted automatically
-- can read as alarming or as trivial depending on the week, and either reading
-- shapes how people behave on the promenade.
--
-- Removing it from the app is NOT enough on its own: the anon key ships inside
-- the page, so anybody could keep calling the RPC directly. The only way to
-- actually withhold it is to stop anon being able to execute it at all, which is
-- what this does.
--
-- WHAT CHANGES
--
--   * bag_theft_summary(integer)      -- DROPPED. Was callable by anyone.
--   * bag_theft_summary(text,integer) -- NEW. Same numbers, admin token required.
--
-- Nothing about the reports themselves changes: swimmers can still lodge one,
-- still get their card and claim code, and can still update it. They just no
-- longer see the totals.
--
-- HOW TO RUN: paste this ENTIRE file into the Supabase SQL editor and Run once.
-- ============================================================================

begin;

-- 1) Remove the public entry point completely.
drop function if exists public.bag_theft_summary(integer);

-- 2) Recreate it behind the same admin token the analytics screens use.
create function public.bag_theft_summary(p_token text, p_days integer default 90)
returns table(
  reports         integer,
  swimmers_value  bigint,
  recovered       integer,
  reported_police integer,
  first_report    timestamptz,
  last_report     timestamptz,
  by_hour         jsonb,   -- {"07": 3, ...} by the START of the theft window
  by_dow          jsonb,   -- {"Mon": 2, ...}
  hotspots        jsonb    -- [{x,y,n}] coarse 12x12 grid over the map image
)
language plpgsql
security definer
set search_path = public
as $function$
declare v_since timestamptz := now() - (greatest(coalesce(p_days, 90), 1) || ' days')::interval;
begin
  if not coalesce(intro_is_admin(p_token), false) then
    raise exception 'not authorised';
  end if;

  return query
  with vis as (
    select * from bag_theft_reports r
     where r.hidden = false and r.status <> 'suspect' and r.created_at >= v_since
  )
  select
    (select count(*)::int from vis),
    (select coalesce(sum(v.value_aud), 0)::bigint from vis v),
    (select count(*)::int from vis v where v.recovered),
    (select count(*)::int from vis v where v.police = 'yes'),
    (select min(v.created_at) from vis v),
    (select max(v.created_at) from vis v),
    coalesce((select jsonb_object_agg(k.h, k.n) from (
       select to_char(v.left_at at time zone 'Australia/Sydney', 'HH24') h, count(*)::int n
         from vis v group by 1) k), '{}'::jsonb),
    coalesce((select jsonb_object_agg(k.d, k.n) from (
       select to_char(v.left_at at time zone 'Australia/Sydney', 'Dy') d, count(*)::int n
         from vis v group by 1) k), '{}'::jsonb),
    coalesce((select jsonb_agg(jsonb_build_object('x', k.gx, 'y', k.gy, 'n', k.n)) from (
       select floor(v.loc_x * 12)::int gx, floor(v.loc_y * 12)::int gy, count(*)::int n
         from vis v where v.loc_x is not null and v.loc_y is not null
        group by 1, 2) k), '[]'::jsonb);
end $function$;

revoke all on function public.bag_theft_summary(text, integer) from public;
grant execute on function public.bag_theft_summary(text, integer) to anon;

commit;

-- Verify anon really is shut out. In the SQL editor you are NOT anon, so test it
-- from the browser console on the live app — this must fail with 404/400:
--
--   fetch(SUPABASE_URL + '/rest/v1/rpc/bag_theft_summary',
--     {method:'POST', headers:{apikey:KEY, Authorization:'Bearer '+KEY,
--      'Content-Type':'application/json'}, body:'{"p_days":90}'}).then(r=>r.status)
--
-- And with a token it should work:
--   select * from public.bag_theft_summary('YOUR_ADMIN_TOKEN', 365);
