-- ============================================================================
-- Metric-name vote (owner, 21 Nov 2026)
-- ============================================================================
-- One-time ballot on the Today tab: keep the three reading names (Entry, Surge,
-- Chop) or rename them (South Steyne, Bower Lane, Wind Chop). The winning choice
-- ships later as its own change; this only collects the votes.
--
-- One row per device. The client sends its raw per-device UUID (swim_device_id)
-- and the function stores md5() of it, the same treatment as feature_votes, so no
-- raw device id is kept. A second vote from the same device replaces the first.
-- No accounts, no names, nothing else about the voter.
--
-- The table itself has RLS on and NO anon policy: the public can neither read
-- nor write rows directly. They go through two security-definer functions:
--   vote_metric_names(p_device, p_choice)  -> records the vote, returns the tally
--   metric_name_tally()                    -> counts only, never device hashes
--
-- See the tally any time in the SQL editor:
--   select * from metric_name_tally();
-- or in the app with ?namevote=1 (shows the card plus the live counts).
--
-- HOW TO RUN: paste this ENTIRE file into the Supabase SQL editor and Run once.
-- Safe to re-run.
-- ============================================================================

begin;

create table if not exists public.metric_name_votes (
  device_hash text primary key,
  choice      text not null check (choice in ('keep', 'rename')),
  voted_at    timestamptz not null default now()
);

alter table public.metric_name_votes enable row level security;
-- deliberately no policies: anon reaches the table only through the functions below

create or replace function public.metric_name_tally()
 returns table (choice text, votes bigint)
 language sql
 security definer
 stable
 set search_path to 'public'
as $function$
  select c.choice, count(v.device_hash)::bigint as votes
    from (values ('keep'), ('rename')) as c(choice)
    left join metric_name_votes v on v.choice = c.choice
   group by c.choice
   order by c.choice;
$function$;

create or replace function public.vote_metric_names(p_device text, p_choice text)
 returns table (choice text, votes bigint)
 language plpgsql
 security definer
 set search_path to 'public'
as $function$
begin
  if p_device is null or char_length(p_device) < 8 or char_length(p_device) > 64 then
    raise exception 'bad device';
  end if;
  if p_choice not in ('keep', 'rename') then raise exception 'bad choice'; end if;

  insert into metric_name_votes (device_hash, choice)
  values (md5(p_device), p_choice)
  on conflict (device_hash) do update set choice = excluded.choice, voted_at = now();

  return query select * from metric_name_tally();
end;
$function$;

revoke all on function public.vote_metric_names(text, text) from public;
revoke all on function public.metric_name_tally() from public;
grant execute on function public.vote_metric_names(text, text) to anon, authenticated;
grant execute on function public.metric_name_tally() to anon, authenticated;

commit;
