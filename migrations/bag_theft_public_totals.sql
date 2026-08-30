-- ============================================================================
-- Bag theft: two headline numbers in public, the rest behind the admin token
-- ============================================================================
-- RUN THIS INSTEAD OF bag_theft_admin_only.sql. That file was never applied,
-- and applying it now would break the strip: it drops the public entry point
-- entirely, and since 29 Aug 2026 the collapsed strip reads its "N reports -
-- $X total" line from exactly that function.
--
-- WHY THIS EXISTS
--
-- Two things are true at once and the old file only handled one of them.
--
--   1. Marco's call, 29 Aug 2026: the scale of the thefts is the point, and a
--      strip that says nothing reads as a form nobody uses. Report count and
--      total value are meant to be public.
--
--   2. The reasoning in bag_theft_admin_only.sql still stands for the REST of
--      the summary. by_hour says what time bags go, by_dow says which days, and
--      hotspots is a 12x12 grid of where they were left. Together that is a
--      shopping list. The report count is not.
--
-- The old file could not separate those because the summary is one function
-- returning one row. This splits it in two.
--
-- URGENCY: putting the totals on the strip made the exposure routine rather
-- than theoretical. Before 29 Aug nothing in the app called the summary, so the
-- hour histogram and hotspot grid only reached someone who went looking for
-- them. The strip now fetches the whole row on every page load, and all of it
-- is in the browser regardless of the two fields actually displayed.
--
-- HOW TO RUN: paste this ENTIRE file into the Supabase SQL editor and press Run.
-- Nothing else needs changing - the app already reads only the two fields the
-- narrow function keeps, so the strip carries on working untouched.
-- ============================================================================

begin;

-- ── 1. the public entry point, narrowed ─────────────────────────────────────
-- Dropped rather than replaced because the return type changes, which
-- CREATE OR REPLACE cannot do. Both statements are in one transaction, so there
-- is no moment where the function is missing.
drop function if exists public.bag_theft_summary(integer);

create function public.bag_theft_summary(p_days integer default 90)
returns table(
  reports         integer,
  swimmers_value  bigint
)
language plpgsql
security definer
set search_path = public
as $function$
declare v_since timestamptz := now() - (greatest(coalesce(p_days, 90), 1) || ' days')::interval;
begin
  -- Same visibility filter as the detailed version: nothing hidden, nothing
  -- flagged suspect. A public total that counted junk reports would be worse
  -- than no total at all.
  return query
  with vis as (
    select * from bag_theft_reports r
     where r.hidden = false and r.status <> 'suspect' and r.created_at >= v_since
  )
  select
    (select count(*)::int from vis),
    (select coalesce(sum(v.value_aud), 0)::bigint from vis v);
end $function$;

grant execute on function public.bag_theft_summary(integer) to anon;
grant execute on function public.bag_theft_summary(integer) to authenticated;


-- ── 2. the detailed summary, admin only ─────────────────────────────────────
-- Body is bag_theft_admin_only.sql's verbatim. PostgREST resolves overloads by
-- the parameter NAMES in the request body, so {p_days} keeps reaching the
-- narrow function above and {p_token, ...} reaches this one. p_token has no
-- default, which is what keeps a bare {p_days} from ever matching here.
create or replace function public.bag_theft_summary(p_token text, p_days integer default 90)
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

-- ── Checking it worked ──────────────────────────────────────────────────────
-- These are SHELL commands, not SQL - they do not go in the editor. Or just ask
-- Claude to run them.
--
--   # public call: should now return ONLY reports and swimmers_value
--   curl -s -X POST "$URL/rest/v1/rpc/bag_theft_summary" \
--     -H "apikey: $ANON" -H "Authorization: Bearer $ANON" \
--     -H "Content-Type: application/json" -d '{"p_days":365}'
--
--   # no by_hour / by_dow / hotspots should appear anywhere in that response
--
-- And in the SQL editor, where you are not anon, the detailed one still works:
--   select * from public.bag_theft_summary('YOUR_ADMIN_TOKEN', 365);
