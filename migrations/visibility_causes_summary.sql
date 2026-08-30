-- ============================================================================
-- Underwater visibility: cause counts for the results panel, and shut anon out
-- of the raw reports table
-- ============================================================================
-- Run AFTER visibility_cause.sql.
--
-- HOW TO RUN: paste this ENTIRE file into the Supabase SQL editor and press Run.
-- ============================================================================

begin;

-- ── 1. cause counts over the same window the bands use ──────────────────────
-- Plain counts, deliberately, not the recency weighting visibility_summary()
-- applies to the bands. "Two people said algae" is a plainer claim than a
-- weighted score, and putting a weighted number next to the bars would invite
-- reading the tags as a share of them, which they are not.
create or replace function public.visibility_causes(p_hours integer default 28)
returns table(
  cause text,
  n     integer
)
language sql
stable
security definer
set search_path = public
as $function$
  select r.cause, count(*)::int
    from visibility_reports r
   where r.cause is not null
     and r.reported_at >= now() - (greatest(coalesce(p_hours, 28), 1) || ' hours')::interval
   group by r.cause
   order by 2 desc, 1;
$function$;

grant execute on function public.visibility_causes(integer) to anon;
grant execute on function public.visibility_causes(integer) to authenticated;


-- ── 2. stop anon reading visibility_reports row by row ──────────────────────
-- WHY THIS IS HERE AND NOT A SEPARATE JOB
--
-- The anon key ships inside the page, and today
--
--     GET /rest/v1/visibility_reports?select=*
--
-- returns every row including ip_hash. That hash is md5(ip || '|swim-visibility'),
-- and IPv4 is only 2^32 values: with the salt known, the whole space can be
-- hashed in minutes on a GPU, so ip_hash is effectively a reversible record of
-- who reported what and when.
--
-- The salt is known. It is in migrations/visibility_cause.sql in this repo,
-- which is public - I put it there when I copied the function body verbatim so
-- the two overloads would agree. Copying it was right; publishing it was the
-- part I got wrong, and this is the fix for it.
--
-- The app never needs these rows: it reads visibility_summary() and now
-- visibility_causes(), both SECURITY DEFINER, which keep working because they
-- run as the owner and bypass RLS. Verified before writing this - index.html
-- contains no reference to the table at all.
--
-- Rotating the salt would NOT help: it only protects hashes written afterwards,
-- and every existing row stays reversible for as long as it is readable.

alter table public.visibility_reports enable row level security;

-- Named policies rather than a blanket revoke, so what is intended stays legible
-- to the next person reading the table's policy list.
drop policy if exists visibility_reports_anon_select on public.visibility_reports;
drop policy if exists visibility_reports_no_public_read on public.visibility_reports;

-- No SELECT policy is created for anon: with RLS on and no permissive policy,
-- direct reads return an empty set instead of the rows.
revoke select on public.visibility_reports from anon;

commit;

-- ── Checking it worked ──────────────────────────────────────────────────────
-- These are SHELL commands, not SQL. Ask Claude to run them, or skip it.
--
--   # should now be [] or a permission error, NOT rows with ip_hash
--   curl -s "$URL/rest/v1/visibility_reports?select=*&limit=1" \
--     -H "apikey: $ANON" -H "Authorization: Bearer $ANON"
--
--   # and the two RPCs must still answer
--   curl -s -X POST "$URL/rest/v1/rpc/visibility_summary" \
--     -H "apikey: $ANON" -H "Authorization: Bearer $ANON" \
--     -H "Content-Type: application/json" -d '{"p_hours":28}'
--
--   curl -s -X POST "$URL/rest/v1/rpc/visibility_causes" \
--     -H "apikey: $ANON" -H "Authorization: Bearer $ANON" \
--     -H "Content-Type: application/json" -d '{"p_hours":28}'
--
-- If the summary stops returning rows after this, it is not SECURITY DEFINER;
-- say so and it can be made so, rather than reopening the table.
--
-- ── Worth considering separately ────────────────────────────────────────────
-- ip_hash is written but nothing appears to use it: report_visibility() states
-- "No cooldown: swimmers may report visibility as often as they like." If
-- visibility_summary() does not use it either, the column could simply be
-- dropped - the safest version of any personal datum is the one not stored.
