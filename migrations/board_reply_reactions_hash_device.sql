-- ============================================================================
-- Align comment reactions with post reactions: store md5(device), not the raw id
-- ============================================================================
-- feature_votes has always stored device_hash = md5(p_device); feature_reply_reactions
-- stored the raw per-device UUID in a column called `device`. Same identifier, two
-- treatments, and only one of them hashed. This brings the reply table across
-- (owner, 21 Sep 2026). Run board_reply_reactions.sql first if the table does not
-- exist yet.
--
-- NOTHING CHANGES FOR A SWIMMER. The client still sends its raw UUID and the
-- functions hash it on arrival, exactly as the post path already does — so the same
-- device keeps matching its own rows and every existing reaction, count and
-- highlighted pick survives. No client change is needed: index.html does not know
-- either way.
--
-- ONE-WAY. md5 cannot be reversed, which is the point, so the raw ids are gone after
-- this. There is no down-migration, and re-running it is safe (see the WHERE below).
--
-- HOW TO RUN: paste this ENTIRE file into the Supabase SQL editor and Run once.
-- Everything is one transaction: if any step fails, nothing changes.
-- ============================================================================

begin;

-- 1) Same column name as feature_votes, so the two tables read alike. The rename
--    carries the primary key (reply_id, device_hash) with it.
alter table public.feature_reply_reactions rename column device to device_hash;

-- 2) Hash what is there. The WHERE is what makes this idempotent AND what makes a
--    half-migrated table self-correcting: an md5 is exactly 32 lowercase hex chars,
--    and no client id is (a UUID carries dashes and is 36). Already-hashed rows are
--    left alone, so running this twice does not double-hash.
--    If two raw ids ever collided into one (reply_id, device_hash) the primary key
--    would abort the whole transaction rather than silently drop a reaction — which
--    is the behaviour to want here.
update public.feature_reply_reactions
   set device_hash = md5(device_hash)
 where device_hash !~ '^[0-9a-f]{32}$';

-- 3) React. Same logic as before — same reaction again removes it, a different one
--    switches — with the hash done here rather than trusted from the caller, and the
--    short-device guard feature_votes has always had. Still returns void (204).
create or replace function public.vote_feature_reply(p_reply_id bigint, p_device text, p_reaction text)
 returns void
 language plpgsql
 security definer
 set search_path to 'public'
as $function$
declare
  v_hash text := md5(p_device);
begin
  if p_device is null or char_length(p_device) < 8 then raise exception 'bad device'; end if;

  delete from feature_reply_reactions
   where reply_id = p_reply_id and device_hash = v_hash and reaction = p_reaction;   -- same click = remove
  if not found then
    insert into feature_reply_reactions (reply_id, device_hash, reaction)
    values (p_reply_id, v_hash, p_reaction)
    on conflict (reply_id, device_hash) do update set reaction = excluded.reaction, created_at = now();  -- switch
  end if;
end $function$;
grant execute on function public.vote_feature_reply(bigint, text, text) to anon;

-- 4) List. Unchanged shape — reply_id, {reaction: count}, my_reaction — comparing the
--    hash. coalesce so a null device asks for counts without claiming any pick,
--    matching list_feature_requests.
create or replace function public.list_feature_reply_reactions(p_device text)
 returns table(reply_id bigint, reactions jsonb, my_reaction text)
 language sql
 security definer
 set search_path to 'public'
as $function$
  select reply_id,
         jsonb_object_agg(reaction, cnt) as reactions,
         (array_agg(reaction) filter (where mine))[1] as my_reaction
  from (select reply_id, reaction, count(*) cnt,
               bool_or(device_hash = md5(coalesce(p_device, ''))) mine
        from feature_reply_reactions group by reply_id, reaction) t
  group by reply_id;
$function$;
grant execute on function public.list_feature_reply_reactions(text) to anon;

commit;

-- ── CHECK IT WORKED (read-only, run after) ─────────────────────────────────
-- Expect: raw = 0, hashed = every row, and the totals unchanged from before.
--   select count(*) filter (where device_hash ~ '^[0-9a-f]{32}$') as hashed,
--          count(*) filter (where device_hash !~ '^[0-9a-f]{32}$') as raw,
--          count(*) as total
--     from public.feature_reply_reactions;
-- Then open Bay Talk: your own reactions must still show as yours (highlighted),
-- which is the real proof the hash matches what the client sends.
