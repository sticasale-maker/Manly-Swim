-- Security hardening from the 15 Oct 2026 audit. Run once in the Supabase SQL
-- editor (owner). Safe to re-run. Four independent parts, one transaction.
--
-- BEFORE RUNNING, check the live definitions still match the files these were
-- rebuilt from (the live DB has run behind migrations/ before):
--
--   select pg_get_functiondef('public.report_bluebottle(text,text)'::regprocedure);
--     -> should match sql/2026-09-28_bluebottle_photo_optional.sql
--   select pg_get_functiondef('public.report_visibility(integer,text)'::regprocedure);
--     -> should match migrations/visibility_cause.sql
--
-- If either differs, stop and send it over: these replace the whole function.
--
-- And see which existing photo URLs the new rule would have refused (new inserts
-- only are checked; existing rows are left as they are, and the apps now refuse to
-- render them):
--
--   select 'bluebottle' t, null::bigint id, photo_url from public.bluebottle_reports
--    where photo_url is not null and photo_url !~ '^https://gkspukabnfbzrvjoewpc\.supabase\.co/storage/v1/object/public/board-images/[A-Za-z0-9._/-]{1,200}$'
--   union all
--   select 'bag', id, photo_url from public.bag_theft_reports
--    where photo_url is not null and photo_url !~ '^https://gkspukabnfbzrvjoewpc\.supabase\.co/storage/v1/object/public/board-images/[A-Za-z0-9._/-]{1,200}$';
--     -> expect no rows

begin;

-- ── 1. Photo URLs: our own bucket, plain file names, nothing else ──────────
-- The old bluebottle check was `like '<prefix>%'`, which let a quote or angle
-- bracket through after the prefix, and report_bag_theft stored p_photo_url as
-- given, so a javascript: URL reached the admin's police pack. A trigger rather
-- than an edit to report_bag_theft, so it holds whichever version of that
-- function is live, and for any writer. A bad URL is dropped, never the report.
create or replace function public.only_own_photo_url()
returns trigger
language plpgsql
set search_path to 'public'
as $function$
begin
  if new.photo_url is not null and new.photo_url !~
     '^https://gkspukabnfbzrvjoewpc\.supabase\.co/storage/v1/object/public/board-images/[A-Za-z0-9._/-]{1,200}$' then
    new.photo_url := null;
  end if;
  return new;
end;
$function$;

drop trigger if exists only_own_photo_url on public.bluebottle_reports;
create trigger only_own_photo_url before insert or update of photo_url
  on public.bluebottle_reports for each row execute function public.only_own_photo_url();

drop trigger if exists only_own_photo_url on public.bag_theft_reports;
create trigger only_own_photo_url before insert or update of photo_url
  on public.bag_theft_reports for each row execute function public.only_own_photo_url();

-- ── 2. report_bluebottle: the real client IP, and the strict photo rule ────
-- The leftmost x-forwarded-for is whatever the caller typed, so a fresh value per
-- request skipped the 4 h cooldown and every report pushes to every subscriber.
-- cf-connecting-ip is set by Cloudflare in front of Supabase and cannot be forged
-- by the caller; same order as report_bag_theft. Otherwise unchanged from
-- sql/2026-09-28_bluebottle_photo_optional.sql.
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
    nullif(split_part(nullif(current_setting('request.headers', true), '')::json ->> 'cf-connecting-ip', ',', 1), ''),
    nullif(split_part(nullif(current_setting('request.headers', true), '')::json ->> 'x-forwarded-for', ',', 1), ''),
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

  -- Only accept a photo URL from our own public bucket with a plain file name. A
  -- missing or foreign URL is stored as null; the report itself is always accepted.
  v_photo := nullif(trim(coalesce(p_photo_url, '')), '');
  if v_photo is not null and v_photo !~
     '^https://gkspukabnfbzrvjoewpc\.supabase\.co/storage/v1/object/public/board-images/[A-Za-z0-9._/-]{1,200}$' then
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

-- ── 3. report_visibility: same IP order ────────────────────────────────────
-- No cooldown here, but ip_hash is what tells one swimmer's votes from many, and
-- a forged header made every vote look like a new swimmer. Otherwise unchanged
-- from migrations/visibility_cause.sql.
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

  if v_cause is not null and p_band > 3 then
    raise exception 'cause only applies to bands 1-3' using errcode = 'P0001';
  end if;

  v_ip := coalesce(
    nullif(split_part(current_setting('request.headers', true)::json ->> 'cf-connecting-ip', ',', 1), ''),
    nullif(split_part(current_setting('request.headers', true)::json ->> 'x-forwarded-for', ',', 1), ''),
    'unknown'
  );
  v_hash := md5(v_ip || '|swim-visibility');

  insert into visibility_reports (band, ip_hash, cause) values (p_band, v_hash, v_cause);
end;
$function$;

grant execute on function public.report_visibility(integer, text) to anon;
grant execute on function public.report_visibility(integer, text) to authenticated;

-- ── 4. save_push_subscription: real push services only, bounded sizes ─────
-- The push Worker POSTs to every stored endpoint, so an arbitrary URL made it
-- fetch anything on the internet, and unbounded fields could bloat the table.
-- Chrome/Edge/Brave/Samsung (FCM), Firefox (Mozilla), Safari (Apple), Windows (WNS).
create or replace function public.save_push_subscription(
  p_endpoint text, p_p256dh text, p_auth text
) returns void
language plpgsql security definer set search_path to 'public'
as $$
begin
  if coalesce(p_endpoint,'') = '' or coalesce(p_p256dh,'') = '' or coalesce(p_auth,'') = '' then
    raise exception 'missing subscription fields';
  end if;
  if char_length(p_endpoint) > 1000 or char_length(p_p256dh) > 200 or char_length(p_auth) > 100
     or p_endpoint !~ '^https://(fcm\.googleapis\.com|updates\.push\.services\.mozilla\.com|[a-z0-9.-]+\.push\.apple\.com|[a-z0-9-]+\.notify\.windows\.com)/' then
    raise exception 'unsupported push endpoint';
  end if;
  insert into public.push_subscriptions (endpoint, p256dh, auth)
  values (p_endpoint, p_p256dh, p_auth)
  on conflict (endpoint) do update
    set p256dh = excluded.p256dh, auth = excluded.auth;
end;
$$;
grant execute on function public.save_push_subscription(text, text, text) to anon, authenticated;

notify pgrst, 'reload schema';

commit;

-- ── After running: confirm, without writing a report ──────────────────────
--   select tgname, tgrelid::regclass from pg_trigger where tgname = 'only_own_photo_url';
--     -> two rows: bluebottle_reports, bag_theft_reports
--   select count(*) from public.push_subscriptions
--    where endpoint !~ '^https://(fcm\.googleapis\.com|updates\.push\.services\.mozilla\.com|[a-z0-9.-]+\.push\.apple\.com|[a-z0-9-]+\.notify\.windows\.com)/';
--     -> 0 is expected; anything else is an existing endpoint the new rule would refuse
