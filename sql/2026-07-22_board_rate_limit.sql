-- Bay Talk (submit_feature_request): per-device cap 5/day -> 20/day on both
-- overloads, and align the 3-arg body limit 50 -> 200 to match the 4-arg.
-- Applied live in Supabase 2026-07-22; committed here so it stops living only
-- in the dashboard. All other logic byte-for-byte unchanged.

CREATE OR REPLACE FUNCTION public.submit_feature_request(p_body text, p_device text, p_name text DEFAULT NULL::text)
 RETURNS bigint
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
declare
  v_body   text := btrim(p_body);
  v_name   text := nullif(left(btrim(coalesce(p_name, '')), 24), '');
  v_hash   text;
  v_id     bigint;
  v_recent int;
begin
  if p_device is null or char_length(p_device) < 8 then raise exception 'bad device'; end if;
  if v_body is null or v_body = '' then raise exception 'empty'; end if;
  if char_length(v_body) > 200 then v_body := left(v_body, 200); end if;
  v_hash := md5(p_device);
  select count(*) into v_recent
  from feature_requests
  where device_hash = v_hash and created_at > now() - interval '1 day';
  if v_recent >= 20 then raise exception 'rate limit'; end if;
  insert into feature_requests(body, device_hash, name)
  values (v_body, v_hash, v_name)
  returning id into v_id;
  insert into feature_votes(request_id, device_hash) values (v_id, v_hash)
  on conflict do nothing;
  return v_id;
end $function$;

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
  if char_length(v_body) > 200 then v_body := left(v_body, 200); end if;
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
