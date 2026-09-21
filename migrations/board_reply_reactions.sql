-- ============================================================================
-- Bay Talk reactions ON A COMMENT  (👍 like · ❤️ love · 😂 laugh · 😮 wow · 😢 sad)
-- ============================================================================
-- The sibling of board_reactions.sql, which covers reactions on a POST. This one
-- covers replies, and it is a CAPTURE OF WHAT IS ALREADY LIVE, written 21 Sep 2026:
-- the table and both functions were created straight in the Supabase dashboard and
-- had never been recorded here, so the repo could not have rebuilt them. Every
-- statement below was read back out of production (pg_get_functiondef,
-- information_schema, pg_constraint) rather than reconstructed from memory.
--
-- RUNNING IT ON THE LIVE DATABASE IS A NO-OP by construction: the table is
-- create-if-not-exists and the functions are create-or-replace with the definitions
-- already in place. It is here so a rebuilt database matches, and so the next change
-- to reply reactions has a file to edit.
--
-- HOW TO RUN: paste this ENTIRE file into the Supabase SQL editor and Run.
-- ============================================================================

begin;

-- 1) The table. PRIMARY KEY (reply_id, device) is what makes a reaction
--    one-per-device-per-reply, and it is the conflict target the switch below uses.
--    ON DELETE CASCADE: deleting a reply takes its reactions with it, so the admin
--    delete in /board-reply cannot leave orphans behind.
create table if not exists public.feature_reply_reactions (
  reply_id   bigint      not null references public.feature_replies(id) on delete cascade,
  device     text        not null,
  reaction   text        not null,
  created_at timestamptz not null default now(),
  primary key (reply_id, device)
);

-- NOTE, not a change: this table stores the RAW device id, while feature_votes
-- stores md5(p_device). Both are the same per-device UUID the app already sends
-- (CLAUDE.md §4), but only the post table hashes it. Left as it is so this file
-- matches production; worth aligning deliberately rather than in a schema capture.

alter table public.feature_reply_reactions
  drop constraint if exists feature_reply_reactions_reaction_check;
alter table public.feature_reply_reactions
  add constraint feature_reply_reactions_reaction_check
  check (reaction = any (array['like'::text, 'love'::text, 'laugh'::text, 'wow'::text, 'sad'::text]));

-- 2) RLS ON, WITH NO POLICIES — deliberately, and it must stay that way.
--    Supabase grants anon and authenticated full table privileges by default, so
--    without this the publishable key could read every device id and delete every
--    reaction straight through PostgREST. RLS with no policy denies all of that; the
--    two functions below are SECURITY DEFINER, so they still work. Verified live on
--    21 Sep 2026: GET /rest/v1/feature_reply_reactions returns [] though rows exist.
--    Do NOT "fix" that empty response by adding a policy — the app never reads this
--    table directly, only through list_feature_reply_reactions.
alter table public.feature_reply_reactions enable row level security;

-- 3) React: same reaction again = remove; a different one = switch; none = insert.
--    Returns VOID, so PostgREST answers 204 with an empty body — the client's rpc()
--    helper has to tolerate that (it did not until 21 Sep 2026, which made every
--    successful reaction report "Couldn't register your reaction").
create or replace function public.vote_feature_reply(p_reply_id bigint, p_device text, p_reaction text)
 returns void
 language plpgsql
 security definer
 set search_path to 'public'
as $function$
begin
  delete from feature_reply_reactions
   where reply_id = p_reply_id and device = p_device and reaction = p_reaction;   -- same click = remove
  if not found then
    insert into feature_reply_reactions (reply_id, device, reaction)
    values (p_reply_id, p_device, p_reaction)
    on conflict (reply_id, device) do update set reaction = excluded.reaction, created_at = now();  -- switch
  end if;
end $function$;
grant execute on function public.vote_feature_reply(bigint, text, text) to anon;

-- 4) List: one row per reply, {reaction: count} plus this device's own pick.
--    Replies with no reactions are simply absent; the client treats a missing row as
--    an empty object, so it does not need them.
create or replace function public.list_feature_reply_reactions(p_device text)
 returns table(reply_id bigint, reactions jsonb, my_reaction text)
 language sql
 security definer
 set search_path to 'public'
as $function$
  select reply_id,
         jsonb_object_agg(reaction, cnt) as reactions,
         (array_agg(reaction) filter (where mine))[1] as my_reaction
  from (select reply_id, reaction, count(*) cnt, bool_or(device = p_device) mine
        from feature_reply_reactions group by reply_id, reaction) t
  group by reply_id;
$function$;
grant execute on function public.list_feature_reply_reactions(text) to anon;

commit;
