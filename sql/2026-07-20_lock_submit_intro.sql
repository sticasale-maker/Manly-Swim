-- Lock down submit_intro: "Who's Out There?" is now read-only (deprecated in the
-- UI, chat moved to Bay Talk). The RPC is unreachable from the client, but revoke
-- execute from the public roles so it can't be called directly either.
-- Reversible — re-grant if ever needed. Revokes across any overload by signature.

do $$
declare r record;
begin
  for r in
    select oid::regprocedure as sig
    from pg_proc
    where proname = 'submit_intro'
      and pronamespace = 'public'::regnamespace
  loop
    execute format('revoke execute on function %s from anon, authenticated', r.sig);
  end loop;
end $$;

-- Verify (should return no rows granting execute to anon/authenticated):
--   select p.proname, r.rolname, has_function_privilege(r.rolname, p.oid, 'execute') AS can_exec
--   from pg_proc p cross join (values ('anon'),('authenticated')) r(rolname)
--   where p.proname = 'submit_intro' and p.pronamespace = 'public'::regnamespace;
