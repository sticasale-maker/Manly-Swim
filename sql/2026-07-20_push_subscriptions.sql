-- Web Push subscriptions for bluebottle alerts.
-- Clients upsert their subscription through save_push_subscription (anon, via a
-- SECURITY DEFINER RPC — the table itself stays locked down by RLS). The push
-- sender (Cloudflare Worker) reads this table with the service_role key.

begin;

create table if not exists public.push_subscriptions (
  endpoint   text primary key,
  p256dh     text not null,
  auth       text not null,
  created_at timestamptz not null default now()
);

-- RLS on, no policies for anon/authenticated → no direct table access.
-- All writes go through the RPCs below; the Worker uses service_role (bypasses RLS).
alter table public.push_subscriptions enable row level security;

-- Upsert a subscription (idempotent on endpoint).
create or replace function public.save_push_subscription(
  p_endpoint text, p_p256dh text, p_auth text
) returns void
language plpgsql security definer set search_path to 'public'
as $$
begin
  if coalesce(p_endpoint,'') = '' or coalesce(p_p256dh,'') = '' or coalesce(p_auth,'') = '' then
    raise exception 'missing subscription fields';
  end if;
  insert into public.push_subscriptions (endpoint, p256dh, auth)
  values (p_endpoint, p_p256dh, p_auth)
  on conflict (endpoint) do update
    set p256dh = excluded.p256dh, auth = excluded.auth;
end;
$$;
grant execute on function public.save_push_subscription(text, text, text) to anon, authenticated;

-- Remove a subscription when the user turns notifications off.
create or replace function public.delete_push_subscription(p_endpoint text)
returns void
language plpgsql security definer set search_path to 'public'
as $$
begin
  delete from public.push_subscriptions where endpoint = p_endpoint;
end;
$$;
grant execute on function public.delete_push_subscription(text) to anon, authenticated;

commit;
