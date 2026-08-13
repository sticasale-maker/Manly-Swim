-- ============================================================================
-- Bag theft: consent, withdrawal, and claim-code-only updates
-- ============================================================================
-- Run AFTER bag_theft_reports.sql and bag_theft_admin_only.sql.
--
-- WHAT THIS CHANGES AND WHY
--
-- 1. WITHDRAWAL. The form now asks for consent to pass reports to police and to
--    use them in aggregate for awareness. Consent that cannot be taken back is
--    not really consent, so a reporter can now withdraw with their claim code.
--    Withdrawal HIDES, it never deletes: the row stays in the record with a
--    timestamp, drops out of every summary, and is flagged in the admin export
--    so it is obvious it must not be passed on. Deleting would leave a hole in
--    an evidence base whose whole value is that it is complete and unedited.
--
-- 2. THE REPORT NUMBER IS NO LONGER NEEDED TO UPDATE. Asking someone for both a
--    report number and a claim code was asking them to keep two things when one
--    identifies the row perfectly well. The claim code is 10 characters from a
--    29-symbol alphabet — about 48 bits — so a collision is not a practical
--    concern, and a lookup by hash is exactly as strict as before: you either
--    hold the code or you do not.
--
-- 3. VALUE AND POLICE STATUS ARE NOW REQUIRED. Both drive decisions — value is
--    what makes a string of incidents legible to police and to the council, and
--    police status tells us how much of the real total is reaching them. A
--    defaulted answer is worse than no answer because it looks like data.
--
-- HOW TO RUN: paste this ENTIRE file into the Supabase SQL editor and Run once.
-- ============================================================================

begin;

-- ---------------------------------------------------------------------------
-- 1) Withdrawal columns
-- ---------------------------------------------------------------------------
alter table public.bag_theft_reports
  add column if not exists withdrawn    boolean not null default false,
  add column if not exists withdrawn_at timestamptz;

-- ---------------------------------------------------------------------------
-- 2) report_bag_theft — value and police status now required
-- ---------------------------------------------------------------------------
drop function if exists public.report_bag_theft(timestamptz, timestamptz, text, double precision, double precision,
  real, real, text, text[], text, integer, boolean, text, text, text, text, text, text, text, text, text);
create function public.report_bag_theft(
  p_left_at       timestamptz,
  p_missed_at     timestamptz,
  p_claim_code    text,
  p_value_aud     integer,                 -- required
  p_police        text,                    -- required
  p_lat           double precision default null,
  p_lng           double precision default null,
  p_loc_x         real    default null,
  p_loc_y         real    default null,
  p_loc_note      text    default null,
  p_items         text[]  default '{}',
  p_items_other   text    default null,
  p_keys_taken    boolean default false,
  p_bag_desc      text    default null,
  p_bag_visible   text    default null,
  p_tracker       text    default 'none',
  p_tracker_ping  text    default null,
  p_event_number  text    default null,
  p_photo_url     text    default null,
  p_what_happened text    default null,
  p_impact        text    default null
)
returns table(id bigint, created_at timestamptz)
language plpgsql
security definer
set search_path = public, extensions
as $function$
declare
  v_ip   text;
  v_hash text;
  v_id   bigint;
  v_at   timestamptz;
  v_n    integer;
begin
  if p_claim_code is null or char_length(p_claim_code) < 8 then
    raise exception 'bad claim code';
  end if;
  if p_left_at is null or p_missed_at is null then
    raise exception 'missing time window';
  end if;
  if p_missed_at < p_left_at then
    raise exception 'window ends before it starts';
  end if;
  if p_left_at > now() + interval '2 hours' or p_left_at < now() - interval '1 year' then
    raise exception 'time window out of range';
  end if;
  if p_value_aud is null or p_value_aud < 0 then
    raise exception 'value required';
  end if;
  if p_police is null or p_police not in ('yes','will','not_yet','no') then
    raise exception 'police status required';
  end if;

  v_ip := coalesce(
    split_part(nullif(current_setting('request.headers', true)::json ->> 'cf-connecting-ip', ''), ',', 1),
    split_part(nullif(current_setting('request.headers', true)::json ->> 'x-forwarded-for', ''), ',', 1),
    'unknown'
  );
  v_hash := encode(extensions.digest('bagtheft:' || v_ip, 'sha256'), 'hex');

  select count(*) into v_n
    from bag_theft_reports r
    where r.ip_hash = v_hash and r.created_at > now() - interval '24 hours';
  if v_n >= 5 then
    raise exception 'too many reports from this connection today';
  end if;

  insert into bag_theft_reports(
    left_at, missed_at, lat, lng, loc_x, loc_y, loc_note,
    items, items_other, value_aud, keys_taken, bag_desc, bag_visible,
    tracker, tracker_ping, police, event_number,
    photo_url, what_happened, impact,
    claim_hash, ip_hash
  ) values (
    p_left_at, p_missed_at, p_lat, p_lng, p_loc_x, p_loc_y, left(p_loc_note, 300),
    coalesce(p_items, '{}'), left(p_items_other, 1000), p_value_aud, coalesce(p_keys_taken, false),
    left(p_bag_desc, 300),
    case when p_bag_visible in ('visible','covered','unsure') then p_bag_visible else null end,
    case when p_tracker in ('none','airtag','tile','other','unsure') then p_tracker else 'none' end,
    left(p_tracker_ping, 500),
    p_police,
    left(nullif(btrim(p_event_number), ''), 60),
    p_photo_url, left(p_what_happened, 2000), left(p_impact, 2000),
    encode(extensions.digest(p_claim_code, 'sha256'), 'hex'), v_hash
  )
  returning bag_theft_reports.id, bag_theft_reports.created_at into v_id, v_at;

  return query select v_id, v_at;
end $function$;
grant execute on function public.report_bag_theft(timestamptz, timestamptz, text, integer, text,
  double precision, double precision, real, real, text, text[], text, boolean, text, text, text,
  text, text, text, text, text) to anon;

-- ---------------------------------------------------------------------------
-- 3) update_bag_theft — claim code alone, plus withdrawal
-- ---------------------------------------------------------------------------
-- Still cannot touch the account of the theft itself. Only: add the Event
-- Number, say it came back, or withdraw it.
drop function if exists public.update_bag_theft(bigint, text, text, boolean, text);
drop function if exists public.update_bag_theft(text, text, boolean, text, boolean);
create function public.update_bag_theft(
  p_claim_code     text,
  p_event_number   text default null,
  p_recovered      boolean default null,
  p_recovered_note text default null,
  p_withdraw       boolean default null
)
returns table(id bigint, event_number text, recovered boolean, withdrawn boolean)
language plpgsql
security definer
set search_path = public, extensions
as $function$
declare
  v_hash text;
  v_id   bigint;
begin
  if p_claim_code is null or char_length(p_claim_code) < 8 then
    raise exception 'bad claim code';
  end if;
  v_hash := encode(extensions.digest(p_claim_code, 'sha256'), 'hex');

  select r.id into v_id from bag_theft_reports r where r.claim_hash = v_hash limit 1;
  if v_id is null then
    raise exception 'no report matches that code';
  end if;

  update bag_theft_reports r set
    event_number   = coalesce(left(nullif(btrim(p_event_number), ''), 60), r.event_number),
    police         = case when nullif(btrim(p_event_number), '') is not null then 'yes' else r.police end,
    recovered      = coalesce(p_recovered, r.recovered),
    recovered_note = coalesce(left(p_recovered_note, 1000), r.recovered_note),
    recovered_at   = case when p_recovered is true and r.recovered is false then now() else r.recovered_at end,
    withdrawn      = coalesce(p_withdraw, r.withdrawn),
    withdrawn_at   = case when p_withdraw is true and r.withdrawn is false then now()
                          when p_withdraw is false then null
                          else r.withdrawn_at end
  where r.id = v_id;

  return query
    select r.id, r.event_number, r.recovered, r.withdrawn
      from bag_theft_reports r where r.id = v_id;
end $function$;
grant execute on function public.update_bag_theft(text, text, boolean, text, boolean) to anon;

-- ---------------------------------------------------------------------------
-- 4) Summary must ignore withdrawn reports
-- ---------------------------------------------------------------------------
create or replace function public.bag_theft_summary(p_token text, p_days integer default 90)
returns table(
  reports         integer,
  swimmers_value  bigint,
  recovered       integer,
  reported_police integer,
  first_report    timestamptz,
  last_report     timestamptz,
  by_hour         jsonb,
  by_dow          jsonb,
  hotspots        jsonb
)
language plpgsql
security definer
set search_path = public
as $function$
declare v_since timestamptz := now() - (greatest(coalesce(p_days, 90), 1) || ' days')::interval;
begin
  if not coalesce(intro_is_admin(p_token), false) then
    raise exception 'not authorised';
  end if;

  return query
  with vis as (
    select * from bag_theft_reports r
     where r.hidden = false and r.withdrawn = false
       and r.status <> 'suspect' and r.created_at >= v_since
  )
  select
    (select count(*)::int from vis),
    (select coalesce(sum(v.value_aud), 0)::bigint from vis v),
    (select count(*)::int from vis v where v.recovered),
    (select count(*)::int from vis v where v.police = 'yes'),
    (select min(v.created_at) from vis v),
    (select max(v.created_at) from vis v),
    coalesce((select jsonb_object_agg(k.h, k.n) from (
       select to_char(v.left_at at time zone 'Australia/Sydney', 'HH24') h, count(*)::int n
         from vis v group by 1) k), '{}'::jsonb),
    coalesce((select jsonb_object_agg(k.d, k.n) from (
       select to_char(v.left_at at time zone 'Australia/Sydney', 'Dy') d, count(*)::int n
         from vis v group by 1) k), '{}'::jsonb),
    coalesce((select jsonb_agg(jsonb_build_object('x', k.gx, 'y', k.gy, 'n', k.n)) from (
       select floor(v.loc_x * 12)::int gx, floor(v.loc_y * 12)::int gy, count(*)::int n
         from vis v where v.loc_x is not null and v.loc_y is not null
        group by 1, 2) k), '[]'::jsonb);
end $function$;

commit;

-- Withdrawn reports still appear in list_bag_thefts (with withdrawn = true) so
-- you can SEE that someone pulled out. They must not be passed to police or used
-- in anything shared onward.
