-- ============================================================================
-- recalibrate_bag_theft_pins — re-derive loc_x/loc_y from lat/lng after a map swap
-- ============================================================================
-- 2026-08-15: the report map was swapped from bag_map.png (1086x1961) to the
-- larger Artboard 1.png (1350x3001), a different crop of the same artwork, with
-- fresh corner calibration. loc_x/loc_y are normalized 0..1 against WHICHEVER
-- image was on screen at submission time — so every row lodged before the swap
-- now plots in the wrong relative spot on the admin heatmap, which shows
-- whatever map is current.
--
-- This does NOT touch the evidentiary record. lat/lng — the real thing, the
-- police export, the number that matters — is untouched. loc_x/loc_y is a
-- pixel-plotting convenience derived FROM lat/lng for the admin map view; this
-- is a maintenance fix to that derived value, not a rewrite of a swimmer's
-- account (which is what the insert-only policy protects — see
-- migrations/bag_theft_reports.sql, point 1).
--
-- Rows with lat/lng null (map wasn't tapped, or was described in words only)
-- are left alone — there is nothing to recompute.
--
-- THE MATH mirrors thfLatLng() in index.html exactly, run backwards. Read that
-- function's comments first if this needs touching again — it is the same
-- similarity transform (rotation + uniform scale + offset), solved as one
-- complex division, inverted here to go lat/lng -> x/y instead of x/y -> lat/lng.
-- If the map is ever swapped again, update A_LAT/A_LNG/B_LAT/B_LNG/ASPECT below
-- to match the new THF_MAP_CAL / _thfMapAspect in index.html, then re-run this
-- against the CURRENT calibration to fix the newly-stale rows.
--
-- HOW TO RUN
--   1. Paste this whole file into the Supabase SQL editor and Run.
--   2. select public.recalibrate_bag_theft_pins('YOUR_ADMIN_TOKEN');
--      Returns the number of rows updated.
-- ============================================================================

create or replace function public.recalibrate_bag_theft_pins(p_token text)
returns integer
language plpgsql
security definer
set search_path = public
as $function$
declare
  -- Current calibration — MUST match THF_MAP_CAL / _thfMapAspect in index.html.
  A_LAT constant double precision := -33.799680552;
  A_LNG constant double precision := 151.290006273;
  B_LAT constant double precision := -33.799253164;
  B_LNG constant double precision := 151.291139506;
  ASPECT constant double precision := 3001.0 / 1350.0;

  v_phi   double precision := A_LAT * pi() / 180;
  v_mLat  double precision;
  v_mLng  double precision;
  v_dX    double precision := 1.0;                 -- b.x - a.x, corners are (0,0)/(1,1)
  v_dY    double precision;                          -- -(b.y - a.y) * ASPECT
  v_den   double precision;
  v_dE    double precision;
  v_dN    double precision;
  v_wR    double precision;
  v_wI    double precision;
  v_magSq double precision;
  v_n     integer;
begin
  if not coalesce(intro_is_admin(p_token), false) then
    raise exception 'not authorised';
  end if;

  v_mLat := 111132.92 - 559.82 * cos(2 * v_phi) + 1.175 * cos(4 * v_phi);
  v_mLng := 111412.84 * cos(v_phi) - 93.5 * cos(3 * v_phi);

  v_dY  := -1.0 * ASPECT;
  v_den := v_dX * v_dX + v_dY * v_dY;

  v_dE := (B_LNG - A_LNG) * v_mLng;
  v_dN := (B_LAT - A_LAT) * v_mLat;

  v_wR := (v_dE * v_dX + v_dN * v_dY) / v_den;
  v_wI := (v_dN * v_dX - v_dE * v_dY) / v_den;
  v_magSq := v_wR * v_wR + v_wI * v_wI;

  with computed as (
    select
      r.id,
      (r.lat - A_LAT) * v_mLat as n,
      (r.lng - A_LNG) * v_mLng as e
    from bag_theft_reports r
    where r.lat is not null and r.lng is not null
  ),
  solved as (
    select
      c.id,
      ((c.e * v_wR + c.n * v_wI) / v_magSq)                          as pX,
      ((c.n * v_wR - c.e * v_wI) / v_magSq)                          as pY
    from computed c
  )
  update bag_theft_reports r
     set loc_x = (s.pX + 0.0)::real,             -- + a.x, which is 0
         loc_y = (0.0 - s.pY / ASPECT)::real      -- a.y - pY/aspect, a.y is 0
    from solved s
   where r.id = s.id;

  get diagnostics v_n = row_count;
  return v_n;
end
$function$;

grant execute on function public.recalibrate_bag_theft_pins(text) to anon;
