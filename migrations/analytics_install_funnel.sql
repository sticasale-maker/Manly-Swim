-- ============================================================================
-- analytics_install_funnel — why do only ~15% of devices install the tile?
-- ============================================================================
-- Feeds the "Install funnel" card in ?stats=1.
--
-- WHAT WAS MISSING
--   The only install signal was the `appinstalled` event, which NEVER FIRES ON iOS
--   — no iOS browser implements it. For a Sydney beach audience that is most of the
--   phones. So the outcome was known (the pwa display-mode flag) but nothing about
--   the path: how many were even offered a tile, how many said no, how many were in
--   a browser where installing is impossible.
--
--   index.html now emits a2hs_shown / a2hs_dismissed / a2hs_cta / a2hs_choice, each
--   with a `variant` (ios | android | android_fallback | android_manual | inapp),
--   and stamps app_open with `inapp`. This reads them back.
--
-- THE NUMBER TO LOOK AT FIRST
--   `cannot_install_pct` — arrivals inside the Facebook or Instagram in-app browser,
--   where iOS offers no "Add to Home Screen" and Android never fires
--   beforeinstallprompt. Those devices are a hard ceiling on the install rate. If
--   that share is large, no amount of in-app prompt tuning will move the number and
--   the fix belongs in how links are posted to Facebook instead.
--
-- HONEST LIMIT
--   `shown_then_installed_pct` is an association, not a conversion rate. People who
--   install are keener to begin with, and on iOS the install itself is invisible —
--   it is inferred from a later standalone launch, which can land outside this
--   window. Read it as a floor.
--
-- Read-only, no new collection beyond the events above, identifies no one.
-- Admin-gated via intro_is_admin.
--
-- HOW TO RUN
--   1. Paste this whole file into the SQL editor and Run → "Success. No rows
--      returned" means the function was created. That is the create step.
--   2. Then call it (returns jsonb, so the wrapper is right here):
--        select jsonb_pretty(public.analytics_install_funnel('YOUR_ADMIN_TOKEN', 30));
-- ============================================================================

create or replace function public.analytics_install_funnel(p_token text, p_days int default 30)
returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  result jsonb;
begin
  if not coalesce(intro_is_admin(p_token), false) then
    raise exception 'not authorised';
  end if;

  with ev as (
    select ae.device_id, ae.event, ae.props, ae.pwa
    from analytics_events ae
    where ae.created_at >= now() - (p_days * interval '1 day')
  ),
  dev as (                                        -- one row per device
    select ev.device_id,
           bool_or(coalesce(ev.pwa, false))                                  as ever_pwa,
           -- Compared as text, never cast: a cast would throw on any unexpected
           -- value, and this is the one prop written by client code.
           bool_or(ev.event = 'app_open' and (ev.props->>'inapp') = 'true')   as ever_inapp,
           bool_or(ev.event = 'a2hs_shown')                                   as saw_prompt,
           bool_or(ev.event = 'a2hs_dismissed')                               as dismissed,
           bool_or(ev.event = 'a2hs_cta')                                     as tapped
    from ev
    group by ev.device_id
  ),
  tot as (
    select count(*)::int                                              as devices,
           count(*) filter (where dev.ever_inapp)::int                as cannot_install,
           count(*) filter (where dev.ever_pwa)::int                  as installed,
           count(*) filter (where dev.saw_prompt)::int                as saw_prompt,
           count(*) filter (where dev.dismissed)::int                 as dismissed,
           count(*) filter (where dev.tapped)::int                    as tapped,
           count(*) filter (where dev.saw_prompt and dev.ever_pwa)::int as shown_then_installed
    from dev
  ),
  variants as (
    select coalesce(ev.props->>'variant', '?')                                        as variant,
           count(distinct ev.device_id) filter (where ev.event = 'a2hs_shown')::int     as shown,
           count(distinct ev.device_id) filter (where ev.event = 'a2hs_dismissed')::int as dismissed,
           count(distinct ev.device_id) filter (where ev.event = 'a2hs_cta')::int       as tapped
    from ev
    where ev.event in ('a2hs_shown', 'a2hs_dismissed', 'a2hs_cta')
    group by 1                                    -- position 1 is the coalesce, not an aggregate
  )
  select jsonb_build_object(
    'devices',            (select devices from tot),
    'cannot_install',     (select cannot_install from tot),
    'cannot_install_pct', (select round(100.0 * cannot_install / greatest(devices, 1), 1) from tot),
    'saw_prompt',         (select saw_prompt from tot),
    'dismissed',          (select dismissed from tot),
    'tapped',             (select tapped from tot),
    'installed',          (select installed from tot),
    'install_pct',        (select round(100.0 * installed / greatest(devices, 1), 1) from tot),
    'shown_then_installed_pct',
                          (select round(100.0 * shown_then_installed / greatest(saw_prompt, 1), 1) from tot),
    'by_variant',         (select jsonb_agg(to_jsonb(variants) order by variants.variant) from variants)
  )
  into result;

  return result;
end
$function$;

grant execute on function public.analytics_install_funnel(text, int) to anon;
