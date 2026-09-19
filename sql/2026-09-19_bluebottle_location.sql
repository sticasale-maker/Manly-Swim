-- Bluebottle reports: WHERE are they — 'sand' or 'water' (19 Sep 2026).
-- The app requires the choice on every new report; the column stays nullable so
-- older reports (and any cached older app build) keep working. Anything other
-- than the two values is stored as null, never rejected, so a report is never lost
-- over this field. Everything else is unchanged from
-- 2026-07-20_bluebottle_photo_required.sql (4 h IP cooldown, own-bucket photo
-- allowlist, photo required to START a warning).
--
-- Run once in the Supabase SQL editor. Safe to re-run.

begin;

alter table public.bluebottle_reports
  add column if not exists location text
  check (location is null or location in ('sand', 'water'));

-- Replace the one-arg function with the two-arg one. Dropping first avoids an
-- ambiguous overload, which PostgREST would refuse to call.
drop function if exists public.report_bluebottle(text);

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

  -- Only accept a photo URL from our own public bucket.
  v_photo := nullif(trim(coalesce(p_photo_url, '')), '');
  if v_photo is not null
     and v_photo not like
       'https://gkspukabnfbzrvjoewpc.supabase.co/storage/v1/object/public/board-images/%' then
    v_photo := null;
  end if;

  -- A bare tap (no photo) may only CONFIRM an active warning. If there is no
  -- photo-backed report in the last 24h, a photo is required to start one.
  if v_photo is null then
    if not exists (
      select 1 from public.bluebottle_reports
      where photo_url is not null
        and reported_at > now() - interval '24 hours'
    ) then
      raise exception 'photo_required' using errcode = 'P0001';
    end if;
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

-- The app reads the new column alongside reported_at and photo_url.
grant select (location) on public.bluebottle_reports to anon, authenticated;

-- Tell PostgREST about the new signature straight away.
notify pgrst, 'reload schema';

commit;
