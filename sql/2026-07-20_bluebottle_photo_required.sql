-- Bluebottle warnings: a photo is REQUIRED to START a warning; once one is
-- active (a photo-backed report exists in the last 24h), others may CONFIRM with
-- a bare tap (no photo). This replaces report_bluebottle with that rule enforced
-- server-side, on top of the existing IP-hash 4h cooldown and the photo-URL
-- allowlist.

begin;

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

  insert into public.bluebottle_reports (ip_hash, reported_at, photo_url)
  values (v_hash, now(), v_photo);

  return 'ok';
end;
$function$;

grant execute on function public.report_bluebottle(text) to anon, authenticated;

commit;
