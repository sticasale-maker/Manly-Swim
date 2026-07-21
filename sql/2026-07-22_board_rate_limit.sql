-- Bay Talk (submit_feature_request + feature_requests) fixes, all applied live
-- in Supabase 2026-07-22. Committed here so the schema stops living only in the
-- dashboard. Three distinct bugs were fixed this day:
--   1. PGRST203 overload ambiguity: the 3-arg and 4-arg overloads both matched a
--      3-key call (p_image_url defaults), so PostgREST returned HTTP 300 and all
--      photo-less posts failed (shown to users as "posting too fast"). Fix: drop
--      the 3-arg overload; the 4-arg (p_name + p_image_url both DEFAULT NULL)
--      covers every client call path.
--   2. Per-device post cap raised 5/day -> 20/day; 3-arg body limit was 50, the
--      surviving 4-arg truncates to 200.
--   3. feature_requests_body_check capped body at 50 chars while the app allows
--      200 (textarea maxlength + RPC truncate). The old 3-arg truncated to 50 so
--      it never tripped; dropping it exposed every 51-200 char post to the
--      constraint. Raised the CHECK to 200.

-- 1 + 2: single 4-arg function, cap 20/day, body 200.
DROP FUNCTION IF EXISTS public.submit_feature_request(p_body text, p_device text, p_name text);

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

-- 3: raise the body length CHECK 50 -> 200 (keep the >=1 floor).
ALTER TABLE public.feature_requests DROP CONSTRAINT feature_requests_body_check;
ALTER TABLE public.feature_requests
  ADD CONSTRAINT feature_requests_body_check
  CHECK (char_length(btrim(body)) >= 1 AND char_length(btrim(body)) <= 200);
