-- Auto-stamp the day on the community water-temperature reading.
--
-- WHY: water_temp_now is a single row updated every morning — by hand in the
-- Supabase editor, and by the Facebook scraper when it runs. `day` is what tells
-- the app how old the reading is, and it only stayed correct if whoever touched
-- temp_c remembered to touch day as well. That is exactly the failure CLAUDE.md §8
-- names: the tile once showed an 11 Aug reading as current for five days.
--
-- Since 20 Aug 2026 the app REFUSES to hand a reading older than two days to the
-- Oracle (index.html, SEA_TEMP_MAX_AGE_D). So a forgotten date cell no longer shows
-- a stale number as fresh — it silently drops a temperature that was in fact
-- updated. Either way the date has to be right, and the only way to guarantee that
-- is to stop asking a person to remember it.
--
-- Sydney date, not UTC: a 06:30 reading is 20:30 the previous day in UTC, so a
-- naive now()::date would stamp yesterday on every morning update through winter.
--
-- Idempotent — safe to run more than once.

create or replace function public.water_temp_stamp_day()
returns trigger
language plpgsql
as $$
begin
  new.day := (now() at time zone 'Australia/Sydney')::date;
  return new;
end;
$$;

drop trigger if exists water_temp_now_stamp on public.water_temp_now;

create trigger water_temp_now_stamp
  before insert or update on public.water_temp_now
  for each row
  execute function public.water_temp_stamp_day();

-- Bring the existing row into line, so the first read after this migration is
-- already dated correctly rather than waiting for the next morning's update.
update public.water_temp_now
   set temp_c = temp_c
 where id = 1;
