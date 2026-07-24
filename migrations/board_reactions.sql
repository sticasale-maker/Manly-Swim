-- ============================================================================
-- Bay Talk multi-reactions  (👍 like · ❤️ love · 😂 laugh · 😮 wow · 😢 sad)
-- ============================================================================
-- Adds a reaction TYPE per device per post to feature_votes and upgrades the two
-- board RPCs. Wrapped in ONE transaction: if anything mismatches your schema the
-- whole thing ROLLS BACK and the existing single-vote system keeps working.
--
-- HOW TO RUN: paste this ENTIRE file into the Supabase SQL editor and Run once.
--   (Do NOT run a single highlighted statement — the DROP/CREATE pairs and the
--    rollback safety only work as a whole.)
--
-- ASSUMPTION: feature_votes has columns (request_id bigint, device text), one row
--   per device per post — matching vote_feature_request(p_request_id, p_device).
--   If your column names differ, tweak them below and re-run (it's transactional,
--   so a wrong guess just errors out and changes nothing).
-- ============================================================================

begin;

-- 1) reaction-type column; every existing vote becomes a 'like'
alter table public.feature_votes
  add column if not exists reaction text not null default 'like';

alter table public.feature_votes drop constraint if exists feature_votes_reaction_chk;
alter table public.feature_votes
  add constraint feature_votes_reaction_chk
  check (reaction in ('like','love','laugh','wow','sad'));

-- one reaction per device per post (the upsert/toggle relies on this)
create unique index if not exists feature_votes_req_dev_uidx
  on public.feature_votes (request_id, device);

-- 2) react: same type again = remove; a different type = switch; none = insert.
--    The client always sends the tapped reaction; the server decides.
drop function if exists public.vote_feature_request(bigint, text);
drop function if exists public.vote_feature_request(bigint, text, text);
create function public.vote_feature_request(p_request_id bigint, p_device text, p_reaction text default 'like')
returns void
language plpgsql
security definer
set search_path = public
as $$
declare v_existing text;
begin
  if p_reaction is null or p_reaction not in ('like','love','laugh','wow','sad') then
    p_reaction := 'like';
  end if;
  select reaction into v_existing
    from feature_votes where request_id = p_request_id and device = p_device;
  if v_existing is null then
    insert into feature_votes (request_id, device, reaction)
      values (p_request_id, p_device, p_reaction);
  elsif v_existing = p_reaction then
    delete from feature_votes where request_id = p_request_id and device = p_device;
  else
    update feature_votes set reaction = p_reaction
      where request_id = p_request_id and device = p_device;
  end if;
end $$;
grant execute on function public.vote_feature_request(bigint, text, text) to anon;

-- 3) list: per-post total (votes), this device's reaction, and per-type counts.
--    Return type changed, so DROP first (CREATE OR REPLACE can't change it).
drop function if exists public.list_feature_requests(text);
create function public.list_feature_requests(p_device text)
returns table(id bigint, votes bigint, voted boolean, reactions jsonb, my_reaction text)
language sql
security definer
set search_path = public
as $$
  with counts as (
    select request_id, reaction, count(*)::int as n
    from feature_votes
    group by request_id, reaction
  ),
  agg as (
    select request_id, sum(n)::bigint as votes, jsonb_object_agg(reaction, n) as reactions
    from counts
    group by request_id
  ),
  mine as (
    select request_id, reaction as my_reaction
    from feature_votes
    where device = p_device
  )
  select r.id,
         coalesce(a.votes, 0)               as votes,
         (m.my_reaction is not null)         as voted,
         coalesce(a.reactions, '{}'::jsonb)  as reactions,
         m.my_reaction
  from feature_requests r
  left join agg  a on a.request_id = r.id
  left join mine m on m.request_id = r.id;
$$;
grant execute on function public.list_feature_requests(text) to anon;

commit;

-- After COMMIT: the app's Bay Talk cards show all five reactions with live
-- per-type counts. Until you run this, the app keeps showing the single 👍.
