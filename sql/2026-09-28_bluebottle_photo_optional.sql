-- Bluebottle reports: the photo is OPTIONAL for every report (28 Sep 2026, per owner).
-- Until now a photo was required to START a warning (no photo-backed report in the
-- last 24 h -> 'photo_required'); a bare tap could only confirm one. That rule is
-- removed. Everything else is unchanged from 2026-09-19_bluebottle_location.sql:
-- the 4 h IP cooldown, the own-bucket photo allowlist, and the sand/water location.
--
-- Push is not decided here: the push-bluebottle Worker (fired by the INSERT
-- webhook) sends for EVERY row, photo or not, since the same day.
--
-- Run once in the Supabase SQL editor. Safe to re-run.

begin;

create or replace function public.report_bluebottle(
  p_photo_url text default null,
  p_location  text default null
)
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
  v_loc    text;
begin
  v_raw_ip := coalesce(
    split_part(
      (nullif(current_setting('request.headers', true), '')::json ->> 'x-forwarded-for'),
      ',', 1),
    (nullif(current_setting('request.headers', true), '')::json ->> 'cf-connecting-ip'),
    'unknown'
  );
  v_hash := md5(coalesce(trim(v_raw_ip), 'unknown'));

  -- Server-side 4-hour cooldown per IP.
  select count(*) into v_recent
  from public.bluebottle_reports
  where ip_hash = v_hash
    and reported_at > now() - interval '4 hours';

  if v_recent > 0 then
    raise exception 'cooldown_active' using errcode = 'P0001';
  end if;

  -- Only accept a photo URL from our own public bucket. A missing or foreign URL
  -- is stored as null; the report itself is always accepted.
  v_photo := nullif(trim(coalesce(p_photo_url, '')), '');
  if v_photo is not null
     and v_photo not like
       'https://gkspukabnfbzrvjoewpc.supabase.co/storage/v1/object/public/board-images/%' then
    v_photo := null;
  end if;

  v_loc := lower(trim(coalesce(p_location, '')));
  if v_loc not in ('sand', 'water') then
    v_loc := null;
  end if;

  insert into public.bluebottle_reports (ip_hash, reported_at, photo_url, location)
  values (v_hash, now(), v_photo, v_loc);

  return 'ok';
end;
$function$;

grant execute on function public.report_bluebottle(text, text) to anon, authenticated;

notify pgrst, 'reload schema';

commit;
