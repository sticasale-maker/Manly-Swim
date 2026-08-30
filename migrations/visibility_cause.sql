-- ============================================================================
-- Underwater visibility: let a swimmer say WHAT the murk was
-- ============================================================================
-- Swimmers asked to be able to report algae, not just "murky". The app offers a
-- second, optional question on the three poor bands (Murky / Hazy / Average):
-- Algae, Sediment, Whale snot, Not sure.
--
-- HOW TO RUN: paste PART 1 into the Supabase SQL editor and Run. PART 2 needs
-- one line filled in first - see the note there.
-- ============================================================================

-- ── PART 1 — the column. Safe to run on its own, changes no behaviour. ───────
begin;

alter table public.visibility_reports
  add column if not exists cause text;

-- Free text would become a tag soup and there is no UI for typing one, so the
-- column is closed to the four answers the app actually offers. NULL stays
-- legal and is the normal case: the question is optional, and every row written
-- before today has no answer to give.
alter table public.visibility_reports
  drop constraint if exists visibility_reports_cause_chk;
alter table public.visibility_reports
  add constraint visibility_reports_cause_chk
  check (cause is null or cause in ('algae', 'sediment', 'snot', 'unsure'));

comment on column public.visibility_reports.cause is
  'Optional swimmer-reported cause of poor visibility: algae | sediment | snot | unsure. NULL = not asked or skipped. Only offered on bands 1-3.';

commit;


-- ── PART 2 — the two-argument RPC ───────────────────────────────────────────
-- The app posts {p_band} today and {p_band, p_cause} once this exists.
-- PostgREST chooses the overload by the parameter NAMES in the body, so adding
-- a two-argument function does NOT disturb the one-argument one: old calls, and
-- any cached client still sending {p_band}, keep resolving to the existing
-- function unchanged. Do not add a DEFAULT to p_cause - that would make
-- {p_band} ambiguous between the two and PostgREST would refuse both.
--
-- WHAT IS MISSING: the body below has to match your existing
-- report_visibility(integer) exactly - the same insert, the same ip_hash
-- derivation and the same cooldown. Guessing the ip_hash expression would
-- fragment the values and quietly break whatever abuse control depends on them,
-- so it is left blank on purpose. Get the current definition with:
--
--     select pg_get_functiondef(p.oid)
--       from pg_proc p join pg_namespace n on n.oid = p.pronamespace
--      where n.nspname = 'public' and p.proname = 'report_visibility';
--
-- then paste it here, add p_cause to the argument list and to the INSERT, and
-- run. Until then the app degrades on its own: it tries the two-argument call,
-- gets PGRST202, and re-sends {p_band} so the band is still recorded and only
-- the cause is lost.
--
-- create or replace function public.report_visibility(p_band integer, p_cause text)
-- returns <same as the existing one>
-- language plpgsql
-- security definer
-- as $$
-- begin
--   -- <body of the existing report_visibility(integer), with cause added to the
--   --  INSERT column list and p_cause to its VALUES>
-- end;
-- $$;
--
-- grant execute on function public.report_visibility(integer, text) to anon;
