-- Bluebottle reports: add an optional photo, so a report can be certified
-- (push-worthy) while a bare tap still counts on the 24h strip.
--
-- Run once in the Supabase SQL editor. Idempotent-ish: the column add is
-- guarded; the function is dropped + recreated inside a transaction so there
-- is no window where report_bluebottle() is missing.

begin;

-- 1. Add the photo column (additive; safe).
alter table public.bluebottle_reports
  add column if not exists photo_url text;

-- 2. Replace the RPC with one that accepts an OPTIONAL p_photo_url.
--    Adding a parameter changes the signature, so drop the old zero-arg
--    function first (CREATE OR REPLACE would leave both as overloads and
--    PostgREST could resolve ambiguously).
drop function if exists public.report_bluebottle();

create or replace function public.report_bluebottle(p_photo_url text default null)
returns text
language plpgsql
security definer
set search_path to 'public'
as $function$
declare
  v_raw_ip text;
  v_hash   text;
  v_recent int;
  v_photo  text;
begin
  -- First IP in x-forwarded-for; fall back to cf-connecting-ip, then 'unknown'.
  v_raw_ip := coalesce(
    split_part(
      (nullif(current_setting('request.headers', true), '')::json ->> 'x-forwarded-for'),
      ',', 1),
    (nullif(current_setting('request.headers', true), '')::json ->> 'cf-connecting-ip'),
    'unknown'
  );
  v_hash := md5(coalesce(trim(v_raw_ip), 'unknown'));

  -- Server-side 4-hour cooldown per IP (unchanged).
  select count(*) into v_recent
  from public.bluebottle_reports
  where ip_hash = v_hash
    and reported_at > now() - interval '4 hours';

  if v_recent > 0 then
    -- App treats any non-2xx as "report failed" and re-enables the button later.
    raise exception 'cooldown_active' using errcode = 'P0001';
  end if;

  -- Only accept a photo URL from OUR public bucket; drop anything else so a
  -- hostile client can't persist an arbitrary URL. NULL is allowed — a bare
  -- tap still counts; only photo-backed reports are push-worthy.
  v_photo := nullif(trim(coalesce(p_photo_url, '')), '');
  if v_photo is not null
     and v_photo not like
       'https://gkspukabnfbzrvjoewpc.supabase.co/storage/v1/object/public/board-images/%' then
    v_photo := null;
  end if;

  insert into public.bluebottle_reports (ip_hash, reported_at, photo_url)
  values (v_hash, now(), v_photo);

  return 'ok';
end;
$function$;

-- Re-grant execute (dropping the function dropped its grants).
grant execute on function public.report_bluebottle(text) to anon, authenticated;

commit;

-- Sanity check after a real photo report:
--   select reported_at, photo_url from public.bluebottle_reports
--   order by reported_at desc limit 10;
