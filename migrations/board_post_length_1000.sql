-- Bay Talk posts: 200 -> 1,000 characters (owner, 11 Oct 2026). RUN BY THE OWNER in the
-- Supabase SQL editor on 11 Oct 2026; recorded here because the function had only ever
-- lived in the dashboard. A user had to cut a post short: the app capped it at 200, the
-- table had a CHECK at 200, and the RPC silently truncated anything longer to 200.
-- The app's limit (index.html: #frInput maxlength, #frCount, featureSubmit) moved with it.
-- Replies are a separate RPC (submit_feature_reply, 500) and were not changed.

begin;

do $$
declare r record;
begin
  for r in
    select conname from pg_constraint
    where conrelid = 'public.feature_requests'::regclass and contype = 'c'
      and pg_get_constraintdef(oid) like '%char_length(btrim(body))%<= 200%'
  loop
    execute format('alter table public.feature_requests drop constraint %I', r.conname);
  end loop;
end $$;

alter table public.feature_requests
  add constraint feature_requests_body_len
  check (char_length(btrim(body)) between 1 and 1000);

CREATE OR REPLACE FUNCTION public.submit_feature_request(p_body text, p_device text, p_name text DEFAULT NULL::text, p_image_url text DEFAULT NULL::text)
 RETURNS bigint
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
declare
  v_body   text := btrim(p_body);
  v_name   text := nullif(left(btrim(coalesce(p_name, '')), 24), '');
  v_image  text := nullif(btrim(coalesce(p_image_url, '')), '');
  v_hash   text;
  v_id     bigint;
  v_recent int;
begin
  if p_device is null or char_length(p_device) < 8 then raise exception 'bad device'; end if;
  if v_body is null or v_body = '' then raise exception 'empty'; end if;
  if char_length(v_body) > 1000 then v_body := left(v_body, 1000); end if;
  if v_image is not null and v_image not like
       'https://gkspukabnfbzrvjoewpc.supabase.co/storage/v1/object/public/board-images/%' then
    v_image := null;
  end if;
  v_hash := md5(p_device);
  select count(*) into v_recent
  from feature_requests
  where device_hash = v_hash and created_at > now() - interval '1 day';
  if v_recent >= 20 then raise exception 'rate limit'; end if;
  insert into feature_requests(body, device_hash, name, image_url)
  values (v_body, v_hash, v_name, v_image)
  returning id into v_id;
  insert into feature_votes(request_id, device_hash) values (v_id, v_hash)
  on conflict do nothing;
  return v_id;
end $function$;

commit;
