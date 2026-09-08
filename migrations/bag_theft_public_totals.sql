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
-- AMENDED 8 Sept 2026 - BOTH summaries now exclude withdrawn reports.
--
-- A reporter can withdraw after lodging (bag_theft_consent_and_claim.sql), and
-- withdrawal hides rather than deletes: the row stays in the record and drops
-- out of every summary. Neither summary here honoured that.
--
--   * The public one never did, so the strip's "N reports - $X" counted a report
--     somebody had pulled out.
--   * The admin one DID - the consent migration added the filter on 14 Aug - but
--     this file rebuilt its body from bag_theft_admin_only.sql "verbatim" on
--     30 Aug, and that file predates withdrawal. The later migration silently
--     reverted the earlier fix. Watch for this whenever a body is copied
--     forward from an older file.
--
-- The police pack (bag-reports.html) was always right: it filters withdrawn in
-- the client and never prints those rows.
--
-- DEPENDENCY, learned the hard way on 8 Sept 2026: bag_theft_consent_and_claim.sql
-- was never applied to the live database, so withdrawn/withdrawn_at did not
-- exist. Adding the filter alone therefore CREATED CLEANLY AND THEN FAILED AT
-- RUNTIME - plpgsql bodies are only syntax-checked at CREATE, not resolved
-- against the catalog - and took the public strip down until the columns were
-- added. Step 0 below now creates them, so this file stands on its own.
--
-- The general lesson: a migration that references a column must either create
-- it or verify it. Do not assume an earlier file in the folder was ever run.
--
-- HOW TO RUN: paste this ENTIRE file into the Supabase SQL editor and press Run.
-- Safe to re-run; every step is idempotent. Nothing else needs changing - the
-- app already reads only the two fields the narrow function keeps, so the strip
-- carries on working untouched.
-- ============================================================================

begin;

-- ── 0. the columns the filters below depend on ──────────────────────────────
-- Additive and idempotent. Withdrawal HIDES rather than deletes, so the default
-- is false and every existing row keeps counting exactly as it did.
alter table public.bag_theft_reports
  add column if not exists withdrawn    boolean not null default false,
  add column if not exists withdrawn_at timestamptz;


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
  -- flagged suspect, nothing withdrawn. A public total that counted junk
  -- reports would be worse than no total at all, and one that counted a report
  -- the reporter has withdrawn is publishing something they took back.
  return query
  with vis as (
    select * from bag_theft_reports r
     where r.hidden = false and r.status <> 'suspect' and r.withdrawn = false
       and r.created_at >= v_since
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

  -- withdrawn = false restored here; see the note at the top of this file.
  return query
  with vis as (
    select * from bag_theft_reports r
     where r.hidden = false and r.status <> 'suspect' and r.withdrawn = false
       and r.created_at >= v_since
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
--
-- To confirm the withdrawn fix specifically, compare the public count against a
-- direct count. They must agree:
--   select (select reports from public.bag_theft_summary(3650)) as summary_says,
--          (select count(*) from bag_theft_reports
--            where hidden = false and status <> 'suspect' and withdrawn = false
--              and created_at >= now() - interval '3650 days') as should_be,
--          (select count(*) from bag_theft_reports where withdrawn) as withdrawn_rows;
