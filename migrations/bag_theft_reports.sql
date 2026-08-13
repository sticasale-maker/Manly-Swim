-- ============================================================================
-- Bag theft reports  (stolen bags on the South Steyne promenade)
-- ============================================================================
-- A bona fide, evidentiary record of bags stolen while swimmers are in the water.
-- Two audiences, two very different views:
--
--   * PUBLIC  (anon)  -> bag_theft_summary()  : counts, time-of-day histogram,
--                        coarse location, total value, recovery rate. NO free
--                        text, NO photos, NO event numbers. Safe to post to the
--                        Bold & Beautiful Facebook group.
--   * POLICE  (admin) -> list_bag_thefts(token): every field, for export.
--
-- WHY IT IS BUILT THIS WAY
--
--  1. INSERT-ONLY. Rows are never deleted and only two things can ever change:
--     the Event Number and the recovery fields, and only by someone holding the
--     claim code. A dataset a reporter can silently rewrite afterwards is worth
--     very little to an investigator; this one has an unbroken server-stamped
--     history.
--
--  2. created_at IS SERVER TIME, always. The client never supplies it. The
--     swimmer-supplied times (left_at / missed_at) are a separate, clearly
--     labelled WINDOW -- we do not know the moment of the theft, only that it
--     happened between those two times. Overlaying those windows across many
--     reports is what reveals when these people actually work.
--
--  3. NO PERSONAL DATA. No name, no email, no phone. Identity is proved by a
--     random claim code, and we store only its SHA-256 -- so even someone
--     holding the anon key cannot read anybody's code out of the table and
--     impersonate them. The plaintext exists only on the reporter's own screen,
--     once.
--
--  4. NO DESCRIPTIONS OF PEOPLE. There is deliberately no suspect field. A
--     crowd-sourced, publicly-summarised list of descriptions of individuals is
--     a defamation and profiling problem, and it is exactly the thing that gets
--     a community dataset dismissed as unreliable. Descriptions of people go to
--     the police, not into this app.
--
--  5. RATE LIMIT IS DELIBERATELY LOOSE (5 per IP per day). These reports are
--     rare and each one matters; wrongly blocking a real victim who is upset and
--     standing on the promenade is far worse than absorbing a duplicate.
--
-- HOW TO RUN: paste this ENTIRE file into the Supabase SQL editor and Run once.
-- ============================================================================

begin;

create extension if not exists pgcrypto with schema extensions;

-- ---------------------------------------------------------------------------
-- 1) The table
-- ---------------------------------------------------------------------------
create table if not exists public.bag_theft_reports (
  id             bigserial primary key,

  -- server truth: when the report reached us. Never client-supplied.
  created_at     timestamptz not null default now(),

  -- the theft WINDOW, as told by the swimmer
  left_at        timestamptz not null,   -- bag put down
  missed_at      timestamptz not null,   -- noticed gone

  -- where. lat/lng are the real thing (for the police export); x/y are the tap
  -- position on the app's map image, kept so we can redraw the pin exactly.
  lat            double precision,
  lng            double precision,
  loc_x          real,                   -- 0..1 across the map image
  loc_y          real,                   -- 0..1 down the map image
  loc_note       text,                   -- "opposite the surf club steps"

  -- what was taken
  items          text[] not null default '{}',   -- flagged from the fixed list
  items_other    text,                           -- free text, police view only
  value_aud      integer,                        -- approximate total
  keys_taken     boolean not null default false,
  bag_desc       text,
  bag_visible    text,                   -- visible | covered | unsure

  -- trackers: the single highest-value field for an investigator, because it
  -- turns "a bag went missing" into a direction of travel.
  tracker        text not null default 'none',   -- none|airtag|tile|other|unsure
  tracker_ping   text,

  -- police
  police         text not null default 'not_yet', -- yes|will|not_yet|no
  event_number   text,                            -- NSW Police Event Number

  -- evidence + human context
  photo_url      text,
  what_happened  text,
  impact         text,

  -- recovery (updatable by claim code)
  recovered      boolean not null default false,
  recovered_note text,
  recovered_at   timestamptz,

  -- identity-without-identity, and abuse control
  claim_hash     text not null,
  ip_hash        text,

  -- moderation. We hide, we never delete: a removed row would be a hole in the
  -- evidence chain. 'suspect' lets us exclude junk from the public summary while
  -- keeping it visible in the police export.
  status         text not null default 'new',     -- new|verified|suspect
  hidden         boolean not null default false
);

alter table public.bag_theft_reports drop constraint if exists bag_theft_window_chk;
alter table public.bag_theft_reports
  add constraint bag_theft_window_chk check (missed_at >= left_at);

alter table public.bag_theft_reports drop constraint if exists bag_theft_police_chk;
alter table public.bag_theft_reports
  add constraint bag_theft_police_chk check (police in ('yes','will','not_yet','no'));

alter table public.bag_theft_reports drop constraint if exists bag_theft_status_chk;
alter table public.bag_theft_reports
  add constraint bag_theft_status_chk check (status in ('new','verified','suspect'));

create index if not exists bag_theft_created_idx on public.bag_theft_reports (created_at desc);
create index if not exists bag_theft_window_idx  on public.bag_theft_reports (left_at);

-- Locked down completely: anon touches this table only through the RPCs below.
alter table public.bag_theft_reports enable row level security;
revoke all on public.bag_theft_reports from anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2) report_bag_theft -- the only way in
-- ---------------------------------------------------------------------------
-- Returns the new id and the server timestamp so the app can render the report
-- card with the real recorded time rather than the phone's clock.
drop function if exists public.report_bag_theft(timestamptz, timestamptz, double precision, double precision,
  real, real, text, text[], text, integer, boolean, text, text, text, text, text, text, text, text, text, text);
create function public.report_bag_theft(
  p_left_at       timestamptz,
  p_missed_at     timestamptz,
  p_claim_code    text,
  p_lat           double precision default null,
  p_lng           double precision default null,
  p_loc_x         real    default null,
  p_loc_y         real    default null,
  p_loc_note      text    default null,
  p_items         text[]  default '{}',
  p_items_other   text    default null,
  p_value_aud     integer default null,
  p_keys_taken    boolean default false,
  p_bag_desc      text    default null,
  p_bag_visible   text    default null,
  p_tracker       text    default 'none',
  p_tracker_ping  text    default null,
  p_police        text    default 'not_yet',
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
  -- a claim code short enough to brute-force would make the update path
  -- worthless, so refuse one outright rather than storing a weak hash.
  if p_claim_code is null or char_length(p_claim_code) < 8 then
    raise exception 'bad claim code';
  end if;
  if p_left_at is null or p_missed_at is null then
    raise exception 'missing time window';
  end if;
  if p_missed_at < p_left_at then
    raise exception 'window ends before it starts';
  end if;
  -- guard against a mistyped year putting a report in 2027 or 1970
  if p_left_at > now() + interval '2 hours' or p_left_at < now() - interval '1 year' then
    raise exception 'time window out of range';
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
    case when p_police in ('yes','will','not_yet','no') then p_police else 'not_yet' end,
    left(nullif(btrim(p_event_number), ''), 60),
    p_photo_url, left(p_what_happened, 2000), left(p_impact, 2000),
    encode(extensions.digest(p_claim_code, 'sha256'), 'hex'), v_hash
  )
  returning bag_theft_reports.id, bag_theft_reports.created_at into v_id, v_at;

  return query select v_id, v_at;
end $function$;
grant execute on function public.report_bag_theft(timestamptz, timestamptz, text, double precision, double precision,
  real, real, text, text[], text, integer, boolean, text, text, text, text, text, text, text, text, text) to anon;

-- ---------------------------------------------------------------------------
-- 3) update_bag_theft -- the claim code earns its keep
-- ---------------------------------------------------------------------------
-- The two things that legitimately change after the fact: an Event Number the
-- swimmer only got once they reached the station, and "I got it back". Nothing
-- else is touchable, so the original account of the theft stays fixed.
drop function if exists public.update_bag_theft(bigint, text, text, boolean, text);
create function public.update_bag_theft(
  p_id             bigint,
  p_claim_code     text,
  p_event_number   text default null,
  p_recovered      boolean default null,
  p_recovered_note text default null
)
returns table(id bigint, event_number text, recovered boolean)
language plpgsql
security definer
set search_path = public, extensions
as $function$
declare v_hash text;
begin
  if p_claim_code is null or char_length(p_claim_code) < 8 then
    raise exception 'bad claim code';
  end if;
  v_hash := encode(extensions.digest(p_claim_code, 'sha256'), 'hex');

  update bag_theft_reports r set
    event_number   = coalesce(left(nullif(btrim(p_event_number), ''), 60), r.event_number),
    police         = case when nullif(btrim(p_event_number), '') is not null then 'yes' else r.police end,
    recovered      = coalesce(p_recovered, r.recovered),
    recovered_note = coalesce(left(p_recovered_note, 1000), r.recovered_note),
    recovered_at   = case when p_recovered is true and r.recovered is false then now() else r.recovered_at end
  where r.id = p_id and r.claim_hash = v_hash;

  if not found then
    raise exception 'no report matches that code';
  end if;

  return query
    select r.id, r.event_number, r.recovered from bag_theft_reports r where r.id = p_id;
end $function$;
grant execute on function public.update_bag_theft(bigint, text, text, boolean, text) to anon;

-- ---------------------------------------------------------------------------
-- 4) bag_theft_summary -- the public / Facebook view
-- ---------------------------------------------------------------------------
-- Aggregate only. Nothing here can identify a swimmer or repeat an accusation:
-- no free text, no photos, no event numbers, and location rounded to a coarse
-- grid so a pin cannot be traced back to one person's spot on the promenade.
drop function if exists public.bag_theft_summary(integer);
create function public.bag_theft_summary(p_days integer default 90)
returns table(
  reports        integer,
  swimmers_value bigint,
  recovered      integer,
  reported_police integer,
  first_report   timestamptz,
  last_report    timestamptz,
  by_hour        jsonb,   -- {"07": 3, "08": 11, ...} start of the theft window
  by_dow         jsonb,   -- {"Mon": 2, ...}
  hotspots       jsonb    -- [{x,y,n}] coarse 12x12 grid over the map image
)
language plpgsql
security definer
set search_path = public
as $function$
declare v_since timestamptz := now() - (greatest(coalesce(p_days, 90), 1) || ' days')::interval;
begin
  return query
  with vis as (
    select * from bag_theft_reports r
     where r.hidden = false and r.status <> 'suspect' and r.created_at >= v_since
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
grant execute on function public.bag_theft_summary(integer) to anon;

-- ---------------------------------------------------------------------------
-- 5) list_bag_thefts -- the police export, admin only
-- ---------------------------------------------------------------------------
-- Everything, newest first, including rows marked 'suspect' (an investigator
-- should decide what is junk, not us) and hidden ones. Gated on the same admin
-- token the Board already uses.
drop function if exists public.list_bag_thefts(text, integer);
create function public.list_bag_thefts(p_token text, p_days integer default 3650)
returns setof public.bag_theft_reports
language plpgsql
security definer
set search_path = public
as $function$
begin
  if not intro_is_admin(p_token) then
    raise exception 'not authorised';
  end if;
  return query
    select * from bag_theft_reports r
     where r.created_at >= now() - (greatest(coalesce(p_days, 3650), 1) || ' days')::interval
     order by r.created_at desc;
end $function$;
grant execute on function public.list_bag_thefts(text, integer) to anon;

commit;

-- After COMMIT: the app can report, update by claim code, and read the public
-- summary. The police export needs the admin token (?board=1 flow).
--
-- Sanity check, safe to run:
--   select * from public.bag_theft_summary(365);
