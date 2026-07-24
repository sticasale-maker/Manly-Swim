-- ============================================================================
-- Bay Talk multi-reactions  (👍 like · ❤️ love · 😂 laugh · 😮 wow · 😢 sad)
-- ============================================================================
-- Matches the REAL schema: feature_votes(request_id bigint, device_hash text),
-- where device_hash = md5(p_device). Adds a reaction TYPE per device per post and
-- upgrades the two RPCs (keeping their existing return columns, just adding
-- reactions + my_reaction to the list one). Wrapped in ONE transaction: if
-- anything fails it ALL rolls back and the current single-vote system is untouched.
--
-- HOW TO RUN: paste this ENTIRE file into the Supabase SQL editor and Run once.
-- ============================================================================

begin;

-- 1) reaction-type column; every existing vote becomes a 'like'
alter table public.feature_votes
  add column if not exists reaction text not null default 'like';

alter table public.feature_votes drop constraint if exists feature_votes_reaction_chk;
alter table public.feature_votes
  add constraint feature_votes_reaction_chk
  check (reaction in ('like','love','laugh','wow','sad'));

-- 2) react: same type again = remove; a different type = switch; none = insert.
--    Keeps the original guard + hash + (request_id, device_hash) key + return shape.
drop function if exists public.vote_feature_request(bigint, text);
drop function if exists public.vote_feature_request(bigint, text, text);
create function public.vote_feature_request(p_request_id bigint, p_device text, p_reaction text default 'like')
returns table(request_id bigint, votes integer, voted boolean)
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_hash     text := md5(p_device);
  v_existing text;
begin
  if p_device is null or char_length(p_device) < 8 then raise exception 'bad device'; end if;
  if p_reaction is null or p_reaction not in ('like','love','laugh','wow','sad') then
    p_reaction := 'like';
  end if;

  select fv.reaction into v_existing
    from feature_votes fv
    where fv.request_id = p_request_id and fv.device_hash = v_hash;

  if v_existing is null then
    insert into feature_votes(request_id, device_hash, reaction)
      values (p_request_id, v_hash, p_reaction)
      on conflict do nothing;
  elsif v_existing = p_reaction then
    delete from feature_votes fv
      where fv.request_id = p_request_id and fv.device_hash = v_hash;
  else
    update feature_votes fv set reaction = p_reaction
      where fv.request_id = p_request_id and fv.device_hash = v_hash;
  end if;

  return query
    select p_request_id,
           (select count(*)::int from feature_votes fv where fv.request_id = p_request_id),
           exists(select 1 from feature_votes fv
                  where fv.request_id = p_request_id and fv.device_hash = v_hash);
end $function$;
grant execute on function public.vote_feature_request(bigint, text, text) to anon;

-- 3) list: same columns as before + reactions (jsonb {type:count}) + my_reaction.
drop function if exists public.list_feature_requests(text);
create function public.list_feature_requests(p_device text)
returns table(id bigint, body text, votes integer, voted boolean, created_at timestamptz,
              reactions jsonb, my_reaction text)
language plpgsql
security definer
set search_path = public
as $function$
declare v_hash text := md5(coalesce(p_device,''));
begin
  return query
    select fr.id, fr.body,
           (select count(*)::int from feature_votes fv where fv.request_id = fr.id) as votes,
           exists(select 1 from feature_votes fv
                  where fv.request_id = fr.id and fv.device_hash = v_hash) as voted,
           fr.created_at,
           coalesce((select jsonb_object_agg(k.reaction, k.n)
                     from (select fv.reaction, count(*)::int n
                           from feature_votes fv
                           where fv.request_id = fr.id
                           group by fv.reaction) k), '{}'::jsonb) as reactions,
           (select fv.reaction from feature_votes fv
            where fv.request_id = fr.id and fv.device_hash = v_hash limit 1) as my_reaction
    from feature_requests fr
    where fr.hidden = false
    order by votes desc, fr.created_at desc
    limit 100;
end $function$;
grant execute on function public.list_feature_requests(text) to anon;

commit;

-- After COMMIT: hard-refresh the app — Bay Talk shows all five reactions with
-- live per-type counts (existing votes carry over as 👍).
