-- ============================================================================
-- Underwater visibility: let a swimmer say WHAT the murk was
-- ============================================================================
-- Swimmers asked to be able to report algae, not just "murky". The app offers a
-- second, optional question on the three poor bands (Murky / Hazy / Average):
-- Algae, Sediment, Whale snot, Not sure.
--
-- HOW TO RUN: paste this ENTIRE file into the Supabase SQL editor and Run once.
-- It is safe to re-run.
-- ============================================================================

begin;

-- ── 1. the column ───────────────────────────────────────────────────────────
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


-- ── 2. the two-argument RPC ─────────────────────────────────────────────────
-- PostgREST chooses the overload by the parameter NAMES in the request body, so
-- this does NOT disturb report_visibility(integer): a bare {p_band} - including
-- from any client still running the old bundle - keeps resolving to it,
-- unchanged. Do NOT give p_cause a DEFAULT: that would make {p_band} ambiguous
-- between the two and PostgREST would refuse both.
--
-- The body is report_visibility(integer) verbatim - same band check, same
-- x-forwarded-for handling, same md5(ip || '|swim-visibility') hash, still no
-- cooldown - with the cause validated and carried into the insert. The hash
-- expression is copied rather than rewritten so both overloads keep producing
-- the SAME ip_hash for the same swimmer.
--
-- Validation RAISES rather than silently coercing, and that is safe: PostgREST
-- returns P0001 as HTTP 400, and the app's visInsert() treats a 400 from the
-- two-argument call as "this cause was not accepted" and immediately re-sends
-- {p_band} on its own. A bad cause therefore costs the cause, never the report.
create or replace function public.report_visibility(p_band integer, p_cause text)
 returns void
 language plpgsql
 security definer
 set search_path to 'public'
as $function$
declare
  v_ip     text;
  v_hash   text;
  v_cause  text;
begin
  if p_band is null or p_band < 1 or p_band > 5 then
    raise exception 'invalid band' using errcode = 'P0001';
  end if;

  v_cause := nullif(btrim(coalesce(p_cause, '')), '');

  if v_cause is not null and v_cause not in ('algae', 'sediment', 'snot', 'unsure') then
    raise exception 'invalid cause' using errcode = 'P0001';
  end if;

  -- The question is only asked on the three poor bands. A cause on Clear or
  -- Epic means the client and the server disagree about something, so refuse it
  -- rather than store a row that cannot be interpreted later.
  if v_cause is not null and p_band > 3 then
    raise exception 'cause only applies to bands 1-3' using errcode = 'P0001';
  end if;

  v_ip := coalesce(
    nullif(split_part(current_setting('request.headers', true)::json ->> 'x-forwarded-for', ',', 1), ''),
    'unknown'
  );
  v_hash := md5(v_ip || '|swim-visibility');

  -- No cooldown: swimmers may report visibility as often as they like.
  insert into visibility_reports (band, ip_hash, cause) values (p_band, v_hash, v_cause);
end;
$function$;

grant execute on function public.report_visibility(integer, text) to anon;
grant execute on function public.report_visibility(integer, text) to authenticated;

commit;

-- ── Checking it worked, without writing a row ───────────────────────────────
-- Sending a deliberately invalid band exercises the overload and returns before
-- the INSERT, so nothing is stored:
--
--   curl -s -X POST "$URL/rest/v1/rpc/report_visibility" \
--     -H "apikey: $ANON" -H "Authorization: Bearer $ANON" \
--     -H "Content-Type: application/json" \
--     -d '{"p_band":99,"p_cause":"algae"}'
--
--   before this migration : {"code":"PGRST202", ... no matches were found ...}
--   after  this migration : {"code":"P0001","message":"invalid band"}
--
-- ── If you ever add a fifth answer ──────────────────────────────────────────
-- Three places have to agree, or reports start bouncing back as 400s and the
-- app quietly falls back to band-only: VIS_CAUSES in index.html, the check
-- constraint above, and the in-function list.
