-- Daily sentence announcements: add a quiet flag to announcements.
-- Run in the Supabase SQL editor (project gkspukabnfbzrvjoewpc).
alter table public.announcements
  add column if not exists quiet boolean not null default false;

-- Then verify the read RPC already returns it:
--   select * from get_active_announcement() limit 1;
-- If 'quiet' is absent, send back:
--   select pg_get_functiondef('get_active_announcement'::regproc);
