#!/usr/bin/env python3
"""
bake_ctbar.py — build species/ctbar.json, the offline fish dataset for the
Cabbage Tree Bay browser.

    pip install anthropic pillow
    set ANTHROPIC_API_KEY=...          # or: ant auth login
    python tools/bake_ctbar.py

Run it from the REPO CLONE, not the Drive copy (CLAUDE.md, "Editing and
deploying"). It writes into species/ and nothing else.

WHY THIS EXISTS, AND WHAT IT DELIBERATELY DOES NOT USE
  There is a hand-built iNaturalist guide for this bay (guide 16243, by John
  Sear) with its own colour tags. This script does not touch it. Everything
  here is derived from the *observation record* inside the reserve polygon —
  which is factual data contributed by 206-odd people — plus our own vision
  pass over their photographs. Consequences, all of them good:

    * 668 species against the guide's 379, every one evidenced by a real local
      record. 306 of them the guide does not list — 83 fish it missed, and the
      rest the animals it never set out to cover: Giant Cuttlefish (281
      records), Southern Eagle Ray (192), Green Sea Turtle (100). Only 17 guide
      species have no local record at all.
    * Photos taken IN this bay, so an animal looks like it looks at Shelly.
    * Attribution is per-photographer, the normal way iNat data is re-used.

  None of that is a criticism of the guide, which is accurate within its scope
  and is 89% John Sear's own photography. We build independently because it
  costs us nothing to, not because there is anything wrong with his work.

SIX STAGES, EACH CACHED ON DISK
  Every stage writes a cache file under .bake-cache/ and is skipped if that
  file is fresh. So a re-run after a crash costs nothing, and a re-run after
  adding one species re-tags only that species.

    1 species  — species_counts at place 71836          (1 call)
    2 months   — per-month species_counts               (12 calls)
    3 photos   — best CC-licensed in-bay photo each     (~427 calls + images)
    4 size     — FishBase bulk TSV, joined offline      (11 MB, once)
    5 tags     — vision pass, Claude → structured attrs (~427 calls)
    6 emit     — species/ctbar.json + species/img/*.webp

  Stage 5 is the only one that costs money. MEASURED: $0.0214/species at Opus-5
  rates — $14.54 all-in for 601 tagged across three passes. (An a-priori guess
  of $7 was out by 3x on input tokens: the injected schema costs far more than
  the prompt text.) Re-runs skip anything already tagged, so widening the scope
  cost only the new species.

FIX THE VOCABULARY BEFORE THE FULL RUN
      python tools/bake_ctbar.py --sample 40

  Tags a stratified sample and prints how each axis partitions the bay, then
  stops without emitting. Do this first. Each of the 427 calls is independent,
  so the enums in FishTags are the only thing keeping them consistent — and
  changing them afterwards re-tags everything AND throws away the human review,
  which is the expensive half. A facet also has to *earn* its place: if one
  value holds most of the species it narrows nothing. John Sear's #brown
  covered 81 of his 379, which is the failure mode to check for.

WHAT COMES FROM WHERE
  The vision pass reports only what is VISIBLE — shape, hue, finish, markings,
  tail, notable fins, and where in the water. It is never asked for size:
  there is no ruler in an underwater photograph.

  Hue and finish are separate axes on purpose. "Silver" is not a colour — a
  silver trevally and a luderick are both grey, and one flashes. The UI can
  still show a chip that says "silver"; it selects grey + silvery underneath.

  Grouping is NOT derived from the photographs. A 40-species sample asked the
  model to count individuals in frame and got "one" 34 times — iNat photos are
  portraits, so the count measured framing, not behaviour. It lives in
  tools/behaviour-overrides.json instead, hand-curated and cited.

  Size comes from FishBase and SeaLifeBase instead (stage 4), because it is
  genuinely independent of colour and shape. Coverage is 486/668 (73%) — good
  for fish, patchy for invertebrates that neither database sizes. The rest stay
  null and render grey.

  Size is TWO tiers, not one. A species is not a single size — juveniles are
  common in a sheltered bay — but letting every species match every bucket it
  could present at destroys the facet: measured, that puts 93% of species in
  "hand-sized". So the adult bucket is the primary match (31/38/24/7/1% across
  the five buckets) and juvenile possibilities appear below a divider. Demote,
  never hide — the same pattern as the "not recorded in September" tail.

  Two guards against a confidently wrong tag, because a wrong attribute makes
  a fish unfindable and the swimmer never learns why:

    photo_usable  — the model may decline a murky or distant shot, and we fall
                    back to the next-best photo for that species.
    confidence    — anything below `high` lands in species/ctbar-review.json
                    for a human to look at. Nothing is silently trusted.

§8 COMPLIANCE (CLAUDE.md, "Absent data is grey")
  The JSON carries `generated_at`, and per species `last_seen`, so the app can
  age and fade every number. `months` is RAW observation counts and `effort`
  is the monthly total across all species — the client divides one by the
  other. Raw counts swing 3.7x across the year because that is when people
  dive, not when fish are there; charting them unnormalised would publish a
  dive-trip calendar labelled as fish behaviour.
"""

import argparse
import base64
import io
import json
import os
import collections
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Optional

from PIL import Image
from pydantic import BaseModel, Field

import anthropic

# ── Constants ────────────────────────────────────────────────────────────────

PLACE_ID = 71836          # Cabbage Tree Bay — the polygon IS the aquatic reserve
# 6744 is AUSTRALIA, not New South Wales. The constant has been named
# NSW_PLACE since the first cut and the app's copy says "read across NSW", so
# every seasonality figure shipped so far is actually pooled across the whole
# continent — Queensland's tropics averaged in with Tasmania's. For a Blue
# Groper, endemic to this coast, the two are nearly the same set. For a Moorish
# Idol they are not remotely.
#
# Corrected 4 Sep 2026. Pooling a season across the continent averages
# Queensland's tropics with Tasmania's, which is meaningless for a species that
# spans both and merely noisy for one that does not. It also plausibly explains
# the mismatches the bay check turned up: Rough Leatherjacket read as a
# September fish across Australia while the bay flatly disagreed, which is what
# continent-wide pooling produces when a species peaks at different times at
# different latitudes.
AU_PLACE = 6744           # Australia
NSW_PLACE = 6825          # New South Wales — what the season should always have used
NSW_PLACE_REAL = NSW_PLACE
# Every animal group a swimmer meets in the bay, not just the ray-finned fish.
# Scoping this to Actinopterygii (which is what "Fishes of Cabbage Tree Bay"
# implies) silently dropped the animals people care about MOST: Spotted
# Wobbegong is the 3rd most-recorded animal here at 332 records, and 10 of the
# 11 species this app already collects sightings for were missing.
TAXA = ",".join([
    "47178",   # Actinopterygii — ray-finned fishes      427 species
    "196614",  # Chondrichthyes — sharks, rays, wobbegongs 25
    "47115",   # Mollusca — cuttlefish, octopus, nudibranchs 105
    "85493",   # Crustacea — crabs, shrimp                 36
    "47534",   # Cnidaria — jellies, anemones, bluebottles  46
    "47549",   # Echinodermata — urchins, sea stars         24
    "26036",   # Reptilia — turtles                          5
])
# Mammalia (40151) is deliberately absent. It held exactly three species — two
# fur seals with 2 records each and a Short-beaked Echidna with 1, recorded on
# the headland path rather than in the water. Four seal records across a decade
# does not earn a slot in a 16-way shape picker, and the echidna is the kind of
# stray a place-based query drags in whenever the polygon touches land.
# Currently empty: dropping Mammalia removed the only stray we had found. Kept
# because the problem recurs — a place query returns whatever was photographed
# inside the polygon, the reserve boundary runs up the shoreline, and Mollusca
# includes land snails. Exclude by id, so every omission is a decision someone
# made on purpose rather than a rule with side effects.
# Only real species belong in a species list. A place query also returns
# observations that were never identified past genus or family — "Swimming
# Crabs", "Seapills and allies" — plus the odd hybrid. 26 of 667 entries were
# of that kind, each with 1-5 records. A swimmer cannot look up a suborder, so
# they are noise in a browser even though they are valid observations.
KEEP_RANKS = {"species", "subspecies"}

EXCLUDE_TAXA = {
    146186,  # Australian Water Dragon — 23 records, a lizard basking on the
             # Shelly rocks. Well recorded, genuinely present, and not something
             # a swimmer will ever be trying to identify underwater.
}

API = "https://api.inaturalist.org/v1"
UA = "ManlySwim/1.0 (https://app.viz.net.au/Manly-Swim/)"

# Licences we may redistribute. Deliberately excludes ND (no derivatives) —
# we crop to a square, which an ND licence does not permit.
PHOTO_LICENCES = "cc0,cc-by,cc-by-nc,cc-by-sa,cc-by-nc-sa"

MODEL = "claude-opus-5"
TAG_WORKERS = 4           # modest; the SDK retries 429s on its own
IMG_PX = 320              # square crop edge, enough for a phone tile at 2x

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "species"
IMG_DIR = OUT_DIR / "img"
# Overridable so a test can exercise a stage without writing over the real
# caches — the other half of the accident above.
CACHE = Path(os.environ.get("BAKE_CACHE_DIR") or (ROOT / ".bake-cache"))

# Opus 5, USD per million tokens. Update if the rate card moves.
PRICE_IN, PRICE_OUT = 5.00, 25.00


class Spend:
    """Running tally of what the vision pass actually costs, so the script can
    report its own bill instead of leaving you to guess from the rate card."""

    def __init__(self):
        self.calls = self.tin = self.tout = 0
        import threading
        self._lock = threading.Lock()

    def add(self, usage) -> None:
        with self._lock:
            self.calls += 1
            self.tin += getattr(usage, "input_tokens", 0) or 0
            self.tout += getattr(usage, "output_tokens", 0) or 0

    @property
    def usd(self) -> float:
        return self.tin / 1e6 * PRICE_IN + self.tout / 1e6 * PRICE_OUT

    def report(self, remaining: int = 0) -> str:
        if not self.calls:
            return "  no API calls made"
        per = self.usd / self.calls
        line = (f"  {self.calls} calls · {self.tin:,} in + {self.tout:,} out"
                f" · ${self.usd:.2f}  (${per:.4f}/species)")
        if remaining:
            line += f"\n  projected for the remaining {remaining}: ${per * remaining:.2f}"
        return line


SPEND = Spend()

# ── Controlled vocabulary ────────────────────────────────────────────────────
# Closed enums, not free text. A filter UI needs a fixed set of chips, and a
# model given free rein will invent forty synonyms for "brownish".

Shape = Literal[
    "torpedo",            # classic streamlined fish — bream, trevally, kingfish
    "deep-flat-sided",    # tall and laterally compressed — old wife, butterflyfish
    "long-slender",       # pencil-shaped — hulafish, garfish, barracuda
    "flat-on-bottom",     # depressed, lies on the seabed — flathead, stargazer
    "ray-like",           # disc with wings — rays, skates
    "eel-like",           # serpentine, no obvious tail fin — morays, congers
    "boxy-odd",           # rigid or odd-shaped — boxfish, pufferfish, seahorses
    # Non-fish. Added when the scope widened: forcing an urchin to pick between
    # "torpedo" and "deep-flat-sided" produces a confidently wrong tag, and a
    # wrong tag makes the animal unfindable. Cheap to add now, expensive later —
    # changing this enum means re-tagging everything.
    "octopus-squid",      # arms, no obvious tail — octopus, cuttlefish, squid
    # Split from "crab-like" for the same reason as the snails: one silhouette
    # was serving Purple Rock Crab, Packhorse Rock Lobster and Hinged-Beaked
    # Prawn. Retiring the old key (rather than keeping it for crabs) is what
    # forces all 17 to be re-examined — the staleness check only fires on values
    # the enum no longer offers.
    "crab",               # wide body, walks sideways, claws out front
    "prawn-lobster",      # long segmented body, tail fan, long antennae
    "sea-star",           # radial arms — sea stars, brittle stars
    "urchin-ball",        # spiny sphere — sea urchins
    # Split from a single "snail-or-shell" after measuring its contents: it
    # held 80 species, 64 with coiled shells and 16 soft slugs, and the one
    # silhouette drawn for it was a slug — representing a fifth of its bucket.
    "shelled-snail",      # coiled or capped shell — volutes, turbans, limpets
    "sea-slug",           # soft, no shell, rhinophores — nudibranchs
    "jelly-or-tentacled", # drifting bell, tentacles — jellies, bluebottles
    "anemone-attached",   # fixed to the rock, waving — anemones, corals
    "turtle-like",        # shelled, flippers — turtles
]
# Hue and finish are SEPARATE axes. "Silver" is not a hue — a silver trevally
# and a luderick are both grey, but one flashes and one doesn't. Collapsing
# them into one list means neither works: you lose the hue of every shiny fish,
# and "silver" quietly becomes a synonym for "I couldn't tell the colour".
#
# The UI still says what a swimmer says. A chip labelled "silver" selects
# hue=grey AND finish=silvery — store precise, present colloquial, exactly as
# the 11 hues collapse into ~6 chips.
Colour = Literal[
    "grey", "white", "black", "brown", "green",
    "blue", "yellow", "orange", "red", "pink", "purple",
]
# Measured on a 40-species sample: offered four values and told to hedge, the
# model filled this for only 25% of species and never once used "translucent"
# or "iridescent". An axis that is null for three species in four cannot be a
# filter chip — tapping it would exclude almost everything.
#
# So it is now a forced binary. "Did it flash?" is answerable from any photo
# and is what a swimmer actually registers.
Finish = Literal[
    "silvery",        # metallic, mirror-like — trevally, mullet, kingfish
    "matte",          # flat, non-reflective — luderick, most reef fish
]
Marking = Literal[
    "plain",
    "bars-across",        # vertical banding, head-to-tail direction crossed
    "stripes-along",      # horizontal, nose-to-tail
    "spots",
    "blotchy-mottled",
    "one-dark-spot",
    "dark-eye-bar",
    "net-or-scribble",
]
Where = Literal["midwater", "over-reef", "on-sand", "in-crevice", "at-surface",
                "attached-to-rock", "unclear"]


class FishTags(BaseModel):
    """What a swimmer could plausibly have registered in two seconds."""
    photo_usable: bool = Field(
        description="False unless you can see ENOUGH OF THE WHOLE ANIMAL to "
                    "judge its body shape. A head-on portrait, a face filling "
                    "the frame, a shot cropped at the gills, a distant blur or "
                    "an animal mostly hidden are all false — however beautiful. "
                    "We keep several photos per species and will try the next "
                    "one, so rejecting a bad reference costs nothing and "
                    "guessing from one costs a wrong tag.")
    shape: Optional[Shape] = None
    colour_primary: Optional[Colour] = None
    colour_secondary: Optional[Colour] = None
    finish: Finish = Field(
        description="Required, pick one. Does the fish read as metallic and "
                    "mirror-like, or flat and non-reflective? Judge against the "
                    "background rather than absolutely, since a flash lifts "
                    "everything.")
    markings: List[Marking] = Field(default_factory=list)
    where: Optional[Where] = None
    # Not surfaced as filter chips today. Extracted anyway because adding an
    # AXIS later means re-tagging all 427 — the pixels have this, your JSON
    # won't. Storing an unused field is free; a second vision pass is not.
    tail_shape: Optional[Literal["forked", "rounded", "pointed", "square",
                                 "whip", "fan", "none-visible"]] = None
    fin_notable: Optional[Literal["tall-dorsal", "trailing-filaments",
                                  "long-pectorals", "spiny-dorsal",
                                  "two-dorsals", "nothing-unusual"]] = None
    distinctive: Optional[str] = Field(
        default=None,
        description="The one feature that clinches the ID, under 12 words, "
                    "plain language. E.g. 'two tall dorsal fins like sails'. "
                    "Null if nothing stands out.")
    confidence: Literal["high", "medium", "low"] = "low"


TAG_SYSTEM = """\
You are tagging fish photographs so that ocean swimmers at Cabbage Tree Bay, \
Sydney can find a species again after a two-second glimpse through a mask.

Describe ONLY what is visible in this photograph. You are not being asked to \
identify the species — the species is already known — and you must not import \
remembered facts about it. If the photo shows a murky shape at distance, that \
is photo_usable: false, not an opportunity to recall what the fish looks like \
in a field guide.

Choose from the supplied vocabulary only.

  shape             the body silhouette, the thing someone actually retains
  colour_primary    the dominant HUE as photographed, allowing for the fact
                    that everything underwater skews blue-green with depth;
                    judge relative to the background, not absolutely. Silver is
                    not a hue — a silvery fish is usually grey or white with
                    finish "silvery"
  finish            silvery or matte — you must choose. Does it flash like a
                    mirror, or is it flat? Judge relative to the background:
                    a flash lifts everything, so compare the fish to the rock
                    and weed around it
  markings          patterns you can see; "bars-across" runs top-to-bottom,
                    "stripes-along" runs nose-to-tail — swimmers confuse these,
                    so be precise
                    A sea slug has no shell and usually wears two horns and a
                    gill tuft; a snail carries a coiled or capped shell. They
                    look nothing alike, so do not merge them. Likewise a crab is
                    wide and walks sideways, while a prawn or lobster is long
                    and segmented with a fanned tail and long antennae.
  where             where the animal is in the frame: resting on sand, tucked
                    in a crevice, hanging in open water, close over reef, or
                    fixed to the rock if it is sessile
  tail_shape        the tail outline, a classic field-guide discriminator.
                    Null for animals that have no tail at all
  fin_notable       any fin that would catch the eye, else "nothing-unusual".
                    Null where fins do not apply
  distinctive       the one feature that would let someone pick it out — an
                    unusual fin, a trailing filament, a posture. Skip it if
                    the fish is unremarkable; a weak hint is worse than none.

Set confidence honestly. "high" only when a swimmer could match this fish from \
your tags alone. A wrong tag makes the species unfindable and the person never \
learns why, so understating confidence is always the cheaper error.

Never comment on size or length. There is no scale reference in the frame."""


# ── Small helpers ────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def slugify(name: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in name]
    out = "".join(keep)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


def assign_slugs(species: list) -> dict:
    """Human-readable slug per species, guaranteed unique.

    A common name is NOT an identifier. Pseudocaranx georgianus (161 records
    here) and Pseudocaranx dentex (1 record) are both "Silver Trevally" on
    iNaturalist — two species, one vernacular name — so keying on the common
    name silently drops one of them. Five more of our species have no common
    name at all and fall back to a bare genus.

    So the stable id is the iNat taxon_id, and this slug is only a friendly
    label for URLs and filenames. Where a slug would collide it takes the
    species epithet, and if that still collides, the taxon_id.
    """
    import collections
    counts = collections.Counter(slugify(s["name"]) for s in species)
    out, used = {}, set()
    for s in species:
        base = slugify(s["name"]) or slugify(s["sci"]) or str(s["taxon_id"])
        slug = base
        if counts[base] > 1:
            epithet = slugify(s["sci"].split()[-1])
            slug = f"{base}-{epithet}" if epithet else base
        if slug in used:
            slug = f"{base}-{s['taxon_id']}"
        used.add(slug)
        out[str(s["taxon_id"])] = slug
    return out


def inat(path: str, **params) -> dict:
    """One iNaturalist API call, with a courteous delay and simple retries."""
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45) as r:
                time.sleep(0.6)          # iNat asks for <1 req/sec sustained
                return json.loads(r.read())
        except Exception as e:            # noqa: BLE001 — any failure is retryable here
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"iNaturalist call failed after retries: {url}\n{last}")


def cache_read(name: str):
    p = CACHE / name
    return json.loads(p.read_text("utf-8")) if p.exists() else None


def cache_write(name: str, data, allow_shrink: bool = False) -> None:
    """Write a cache, refusing to silently gut one.

    Stages take a `tags` dict and write it back wholesale. Hand one a SUBSET —
    as a test harness did — and it writes the subset over the whole cache. That
    cost 603 tags, every shape ballot, and about $14 of re-tagging, and nothing
    complained: the next run simply saw 6 cached entries and rebuilt the rest.

    So a write that loses more than half the entries now has to say it means
    it. Legitimate shrinks are small (the staleness check drops the species a
    vocabulary change invalidated — 13% at its largest) and pass untouched.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / name
    if p.exists() and isinstance(data, dict) and not allow_shrink:
        try:
            old = json.loads(p.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            old = None
        if isinstance(old, dict) and len(old) >= 20 and len(data) < len(old) / 2:
            raise RuntimeError(
                f"refusing to write {name}: {len(data)} entries would replace "
                f"{len(old)}. If a subset is genuinely intended, pass "
                f"allow_shrink=True; if this is a test, point CACHE somewhere "
                f"else with BAKE_CACHE_DIR.")
    p.write_text(json.dumps(data), encoding="utf-8")


def notify_failure(msg: str) -> None:
    """Ping the same ntfy topic the Facebook scrapers use, so a silent
    quarterly cron failure surfaces somewhere a human looks."""
    topic = os.environ.get("NTFY_TOPIC", "FBscrapefailed")
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}",
            data=f"bake_ctbar failed: {msg}"[:900].encode("utf-8"),
            headers={"Title": "Manly Swim — fish bake failed", "Priority": "high"},
        )
        urllib.request.urlopen(req, timeout=15).read()
    except Exception:                     # noqa: BLE001 — alerting must never mask the real error
        pass


# ── Stage 1: the species list ────────────────────────────────────────────────

def stage_species(force: bool) -> list:
    cached = None if force else cache_read("species.json")
    if cached:
        log(f"1/6 species      {len(cached):>4} (cached)")
        return cached

    # iNat caps per_page at 500. Widening the taxon scope took us to 671
    # species, so a single page silently dropped 171 of them — the guard below
    # caught it, but only because it was there. Page until the list is whole.
    import collections
    dropped = collections.Counter()
    out, page = [], 1
    while True:
        d = inat("observations/species_counts", place_id=PLACE_ID, taxon_id=TAXA,
                 per_page=500, page=page, verifiable="true")
        for r in d["results"]:
            t = r["taxon"]
            if t["id"] in EXCLUDE_TAXA:
                log(f"    excluded: {t.get('preferred_common_name') or t['name']}")
                continue
            if t.get("rank") not in KEEP_RANKS:
                dropped[t.get("rank") or "?"] += 1
                continue
            out.append({
                "taxon_id": t["id"],
                "name": t.get("preferred_common_name") or t["name"],
                "sci": t["name"],
                "annual": r["count"],
            })
        total = d["total_results"]
        if len(out) >= total or not d["results"]:
            break
        page += 1
        log(f"    page {page} … {len(out)}/{total}")

    if dropped:
        log("    dropped as not-a-species: "
            + ", ".join(f"{v} {k}" for k, v in dropped.most_common()))
    if len(out) + sum(dropped.values()) < total:
        log(f"    WARNING: {total} reported, only {len(out)} kept "
            f"+ {sum(dropped.values())} dropped")
    cache_write("species.json", out)
    log(f"1/6 species      {len(out):>4}")
    return out


# ── Stage 2: seasonality ─────────────────────────────────────────────────────

def stage_months(species: list, force: bool) -> dict:
    cached = None if force else cache_read("months.json")
    if cached:
        log("2/6 months       12 (cached)")
        return cached

    by_taxon = {s["taxon_id"]: [0] * 12 for s in species}
    effort = [0] * 12
    for m in range(1, 13):
        seen, page = 0, 1
        while True:
            d = inat("observations/species_counts", place_id=PLACE_ID, taxon_id=TAXA,
                     per_page=500, page=page, verifiable="true", month=m)
            for r in d["results"]:
                tid = r["taxon"]["id"]
                # Effort sums EVERY species in the response, not just the ones
                # we kept. Summing the filtered set instead would make --limit
                # runs silently produce a wrong seasonality denominator — and
                # truncating at 500 would do the same, quietly.
                effort[m - 1] += r["count"]
                if tid in by_taxon:
                    by_taxon[tid][m - 1] = r["count"]
            seen += len(d["results"])
            if seen >= d["total_results"] or not d["results"]:
                break
            page += 1
        log(f"    month {m:>2}  {d['total_results']:>3} species")
    out = {"by_taxon": {str(k): v for k, v in by_taxon.items()}, "effort": effort}
    cache_write("months.json", out)
    log(f"2/6 months       effort {min(effort)}–{max(effort)} "
        f"({max(effort) / max(1, min(effort)):.1f}x swing)")
    return out


# ── Stage 3: one re-usable in-bay photograph per species ─────────────────────

# THE PHOTO POOL IS NSW, NOT THE BAY.
#
# It was the bay for one bad reason: the first cut set place_id on the photo
# query and everything downstream inherited it, including a caption that read
# "taken in this bay". Nobody asked for that. A photograph is here to show what
# the species looks like; the bay RECORDS are what tie it to this water, and
# they are unaffected. Plectaster decanus photographed at Bare Island is the
# same animal as one off Shelly.
#
# The restriction cost real quality. Cabbage Tree Bay yields a handful of
# re-usable shots per species and sometimes none — 37 species were hidden
# outright for want of a licence rather than a sighting, and the poor photos
# that drove the whole hand-override mechanism (a head-on Port Jackson, an
# algae-covered Eastern Fortescue) were poor because they were the only ones
# there. Across NSW the pool is often hundreds deep.
#
# The caption names where the shot was taken, because the old one asserted the
# bay and that must not sit under a photograph from elsewhere.
PHOTO_PLACE = NSW_PLACE_REAL

# Every species gets a picture, or the app is hiding animals it knows about.
# The ladder loosens one constraint at a time and records which rung it landed
# on, so a shot that is merely the best available is not passed off as the best.
# Research grade is given up before the licence is, because an unverified
# identification risks showing the WRONG ANIMAL — the failure that makes an ID
# guide worthless — while a distant photo of the right one still teaches you
# what it looks like.
PHOTO_TIERS = [
    ("nsw",        dict(place_id=NSW_PLACE_REAL, quality_grade="research")),
    ("australia",  dict(place_id=AU_PLACE,       quality_grade="research")),
    ("anywhere",   dict(quality_grade="research")),
    ("unverified", dict()),
]


def fetch_photo_candidates(taxon_id: int, n: int = 3) -> list:
    """Best CC-licensed photos of this species, loosening the net until one
    turns up. Best-voted first within each rung."""
    for tier, extra in PHOTO_TIERS:
        d = inat("observations", taxon_id=taxon_id,
                 photo_license=PHOTO_LICENCES, order_by="votes",
                 per_page=n, **extra)
        out = []
        for o in d.get("results", []):
            for p in o.get("photos", [])[:1]:
                out.append({
                    "photo_id": p["id"],
                    "url": p["url"].replace("/square.", "/medium."),
                    "who": o["user"]["login"],
                    "who_name": o["user"].get("name") or "",
                    "lic": (p.get("license_code") or "").upper(),
                    "obs_id": o["id"],
                    "observed_on": o.get("observed_on"),
                    "place": (o.get("place_guess") or "").split(",")[0].strip(),
                    "tier": tier,
                })
        if out:
            return out
    return []


def download_square(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        raw = urllib.request.urlopen(req, timeout=45).read()
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        s = min(im.size)
        im = im.crop(((im.width - s) // 2, (im.height - s) // 2,
                      (im.width - s) // 2 + s, (im.height - s) // 2 + s))
        im = im.resize((IMG_PX, IMG_PX), Image.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        im.save(dest, "WEBP", quality=78, method=5)
        return True
    except Exception as e:                # noqa: BLE001
        log(f"    photo failed {url}: {e}")
        return False


def stage_photos(species: list, force: bool, slugs: dict) -> dict:
    # Incremental, NOT all-or-nothing. A cache built by an earlier `--limit`
    # run covers only those species; treating its presence as "done" would
    # silently ship a handful of photos on the next full run. So we look up
    # what's missing and fetch only that.
    out = {} if force else (cache_read("photos.json") or {})
    seen_none = set(out.get("_none", [])) if isinstance(out.get("_none"), list) else set()
    out.pop("_none", None)

    todo = [s for s in species
            if str(s["taxon_id"]) not in out and str(s["taxon_id"]) not in seen_none]
    if not todo:
        log(f"3/6 photos       {len(out):>4} (cached)")
        return out

    # Hand-picked photos win. Community votes reward a striking picture, not a
    # legible one — the Eastern Fortescue's best-voted shot is an algae-covered
    # blob that three separate checks flagged as unidentifiable. One line here
    # beats arguing with the ranking.
    picks = {}
    ppath = ROOT / "tools" / "photo-overrides.json"
    if ppath.exists():
        picks = {k: v for k, v in json.loads(ppath.read_text("utf-8")).items()
                 if not k.startswith("_")}

    log(f"3/6 photos       {len(todo)} to fetch ({len(out)} cached)")
    for i, s in enumerate(todo, 1):
        cands = fetch_photo_candidates(s["taxon_id"])
        if not cands:
            seen_none.add(str(s["taxon_id"]))   # remember the miss, don't re-ask
            continue
        slug = slugs[str(s["taxon_id"])]
        chosen = picks.get(s["sci"])
        if chosen:
            # put the hand-picked observation's photo at the head of the list
            try:
                o = inat(f"observations/{chosen}")["results"][0]
                ph = o["photos"][0]
                # The attributes were read off the OLD photo and must not
                # survive a hand-pick — but deleting the whole record here is
                # the wrong instrument, and it cost ten species their shape.
                # A tag holds two different kinds of thing: a description of a
                # photograph, which is now wrong, and a shape asked from the
                # species name, which is not.
                #
                # stage_tags already drops a record whose photo has left the
                # candidate list, and it does so through kept_shape, which
                # carries the shape across. Leave the record alone and let the
                # staleness rule take it.
                cands.insert(0, {
                    "place": (o.get("place_guess") or "").split(",")[0].strip(),
                    "photo_id": ph["id"],
                    "url": ph["url"].replace("/square.", "/medium."),
                    "who": o["user"]["login"],
                    "who_name": o["user"].get("name") or "",
                    "lic": (ph.get("license_code") or "").upper(),
                    "obs_id": o["id"],
                    "observed_on": o.get("observed_on"),
                })
                log(f"    hand-picked photo for {s['name']}")
            except Exception as e:                     # noqa: BLE001
                log(f"    photo override failed for {s['name']}: {e}")
        # Keep every candidate: if the vision pass rejects the first as
        # unusable we retry with the next rather than dropping the species.
        for c in cands:
            c["file"] = f"img/{slug}-{c['photo_id']}.webp"
        out[str(s["taxon_id"])] = cands
        if i % 25 == 0:
            log(f"    {i}/{len(todo)} scanned")
            cache_write("photos.json", {**out, "_none": sorted(seen_none)})

    cache_write("photos.json", {**out, "_none": sorted(seen_none)})
    log(f"3/6 photos       {len(out):>4} species have a photo"
        f"  ({len(seen_none)} have none)")
    return out


# ── Stage 4: size, from FishBase ─────────────────────────────────────────────
#
# Size is the one axis a photograph cannot supply and the one that narrows
# hardest, because it is genuinely independent of colour and shape. FishBase
# publishes its whole database as bulk TSV on GitHub, so this is a one-off
# 11 MB download and then an offline join — no API, no rate limit, no key.
#
# Coverage measured against our 427: 386 direct, 11 via synonym, 5 via a unique
# species epithet = 402 (94%). The residue is post-2021 genus recombinations
# (Pycnochromis <- Chromis, Plectroglyphidodon <- Stegastes, Ferdauia <-
# Carangoides) that the 21.06 release predates. Those stay null and render grey
# rather than guessed — §8 again.

FB_RELEASE = "fb-21.06"       # FishBase — fishes
SLB_RELEASE = "slb-21.08"     # SeaLifeBase — molluscs, crustaceans, everything else
FB_BASE = "https://github.com/ropensci/rfishbase/releases/download"

# Bucket floors in cm, in swimmer's units. A person judges against their own
# body, not a tape measure.
# Anchored to the body part each label names, which the first cut was not: a
# 70 cm Blue Groper was landing in "as long as you" because that band started
# at 70. An arm is about 75 cm, a person about 170, so the boundaries now sit
# where the words actually mean something.
SIZE_BUCKETS = [
    ("hand", 0, 20, "hand-sized"),
    ("forearm", 20, 45, "forearm"),
    ("arm", 45, 90, "arm's length"),
    ("you", 90, 180, "as long as you"),
    ("bigger", 180, 10_000, "bigger than you"),
]

# A species is not one size — juveniles of a big fish are common in a sheltered
# bay. But letting every species match every bucket it could present at
# destroys the facet: measured over the 402 species we have sizes for, a 0.2
# juvenile fraction puts 93% of them in "hand-sized". That narrows nothing.
#
# So size is TWO tiers, the same shape as the "not recorded in September" tail:
#   primary   — the adult bucket. Discriminates well (31/38/24/7/1% across the
#               five buckets), and is what the swimmer almost always meant.
#   secondary — species that could be this size AS A JUVENILE, shown below a
#               divider rather than excluded. Never hide a real possibility;
#               demote it.
# The fraction is a modelling assumption, not a measurement.
JUVENILE_FRACTION = 0.2


def _fb_download(fname: str, release: str = FB_RELEASE) -> Path:
    dest = CACHE / f"{release}-{fname}"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    CACHE.mkdir(parents=True, exist_ok=True)
    url = f"{FB_BASE}/{release}/{fname}"
    log(f"    downloading {fname} …")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as r:
        dest.write_bytes(r.read())
    return dest


def _fb_rows(path: Path):
    import csv as _csv
    import gzip as _gzip
    _csv.field_size_limit(10 ** 7)
    with _gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        yield from _csv.DictReader(f, delimiter="\t")


def _cm(v) -> Optional[float]:
    try:
        x = float(v)
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


# Genus moves since the FishBase/SeaLifeBase releases. Explicit, because the
# alternative heuristics both fail: matching on a unique species epithet gave
# the Oak Chiton (Onithochiton quercinus) the length of a cone snail (Conus
# quercinus) — unique only because the chiton is absent from the database —
# and checking family cannot work, since our genus is missing from the data by
# definition, which is why we are here.
#
# Every entry is a decision someone made and can be checked. Scientific names
# throughout: common names collide (two species here are both "Silver
# Trevally") and are not identifiers.
GENUS_MOVES = {
    "Ascarosepion":       "Sepia",         # cuttlefish, 2023
    "Plectroglyphidodon": "Stegastes",     # damselfish
    "Pycnochromis":       "Chromis",       # damselfish
    "Azurina":            "Chromis",       # damselfish
    "Ferdauia":           "Carangoides",   # trevally
    "Platycaranx":        "Carangoides",   # trevally
    "Verconia":           "Noumea",        # nudibranch
}


def _known_move(sci: str, cand: dict) -> bool:
    """Only accept an epithet match when the candidate sits in the genus this
    species is actually recorded as having moved from."""
    return GENUS_MOVES.get(sci.split()[0]) == (cand.get("Genus") or "").strip()


def stage_size(species: list, force: bool, typical: dict = None) -> dict:
    cached = None if force else cache_read("size.json")
    if cached:
        log(f"4/6 size         {len(cached):>4} (cached)")
        return cached

    import collections
    by_name, by_code, by_epithet = {}, {}, collections.defaultdict(list)
    # FishBase first, then SeaLifeBase for the non-fish. FishBase wins on a
    # clash: it is the authority for anything with fins.
    for rel in (SLB_RELEASE, FB_RELEASE):
        for r in _fb_rows(_fb_download("species.tsv.gz", rel)):
            g, s = (r.get("Genus") or "").strip(), (r.get("Species") or "").strip()
            r["_src"] = rel
            if g and s:
                by_name[f"{g} {s}"] = r
                by_epithet[s].append(r)
            if r.get("SpecCode"):
                by_code[f'{rel}:{r["SpecCode"].strip()}'] = r

    syn = {}
    for rel in (SLB_RELEASE, FB_RELEASE):
        for r in _fb_rows(_fb_download("synonyms.tsv.gz", rel)):
            nm = ((r.get("SynGenus") or "").strip() + " " +
                  (r.get("SynSpecies") or "").strip()).strip()
            code = (r.get("SpecCode") or "").strip()
            if nm and code:
                syn.setdefault(nm, f"{rel}:{code}")

    # The shipped template lists every unmatched species with a null value, as
    # a fill-in-the-blanks task. So drop the nulls and the "_README" keys here —
    # an unfilled placeholder means "no size", not "size of None".
    overrides = {}
    ov_path = ROOT / "tools" / "size-overrides.json"
    if ov_path.exists():
        overrides = {k: v for k, v in json.loads(ov_path.read_text("utf-8")).items()
                     if not k.startswith("_") and isinstance(v, (int, float))}

    out, how_tally = {}, collections.Counter()   # match types grow; don't fix the keys
    for s in species:
        sci = s["sci"]
        rec = None
        if sci in overrides:
            mx, how, typ = float(overrides[sci]), "override", "manual"
        else:
            rec, how = by_name.get(sci), "direct"
            if rec is None and sci in syn:
                rec, how = by_code.get(syn[sci]), "synonym"
            if rec is None:
                # Last resort for post-2021 genus moves — Ascarosepion <- Sepia,
                # Plectroglyphidodon <- Stegastes, Pycnochromis <- Chromis.
                #
                # Uniqueness alone is NOT enough: Onithochiton quercinus is a
                # chiton, Conus quercinus a cone snail, and the epithet was
                # "unique" only because the chiton is absent from the data. So
                # the move has to be one we have recorded, by name.
                cands = by_epithet.get(sci.split()[-1], [])
                if len(cands) == 1 and _known_move(sci, cands[0]):
                    rec, how = cands[0], "epithet"
            # A researched typical length is a SOURCE in its own right, not just
            # a refinement of a FishBase figure. Bailing out here when FishBase
            # had no match threw away the lookups for Southern Eagle Ray, Silver
            # Trevally and Eastern Hulafish — the post-2021 recombinations that
            # were the whole reason for doing the research.
            tpre = (typical or {}).get(str(s["taxon_id"])) or {}
            if rec is None:
                if not (tpre.get("status") == "found" and tpre.get("typical_cm")):
                    continue
                mx, typ, how = float(tpre["typical_cm"]), "typical", "researched"
                rec = {}
            else:
                common, mxl = _cm(rec.get("CommonLength")), _cm(rec.get("Length"))
                typ = "common" if common else "max"
                mx = common or mxl
                if mx is None:
                    # FishBase knows the species but carries no length at all.
                    # Bailing out here threw away the researched typical for
                    # Eastern Hulafish, Long-spined Sea Urchin, Purple Rock Crab
                    # and Eastern Blue Devil — every one of them a species we
                    # had already paid to look up. A matched row with no number
                    # is worth less than a number from anywhere else.
                    if tpre.get("status") == "found" and tpre.get("typical_cm"):
                        mx, typ, how = float(tpre["typical_cm"]), "typical", "researched"
                    else:
                        continue

        # A researched typical length wins the BUCKET; the sourced max stays as
        # the displayed "grows to" figure. Two different jobs, two numbers.
        tp = (typical or {}).get(str(s["taxon_id"])) or {}
        display_max = _cm(tp.get("max_cm")) or (mx if typ != "typical" else None)
        if tp.get("status") == "found" and tp.get("typical_cm"):
            mx, typ = float(tp["typical_cm"]), "typical"
        lo = max(1.0, round(mx * JUVENILE_FRACTION))
        adult = next((k for k, blo, bhi, _ in SIZE_BUCKETS if blo <= mx < bhi),
                     SIZE_BUCKETS[-1][0])
        juvenile = [k for k, blo, bhi, _ in SIZE_BUCKETS
                    if lo < bhi and mx >= blo and k != adult]
        out[str(s["taxon_id"])] = {
            "cm": round(mx, 1),
            "max_cm": round(display_max, 1) if display_max else None,
            "typical_source": tp.get("source_url") if typ == "typical" else None,
            "typical_estimated": bool(tp.get("estimated")) if typ == "typical" else False,
            # A sea star's 15 cm is an arm span and a chiton's is a shell; saying
            # "15 cm long" for either is wrong. Carried so the app can say which.
            "measure": tp.get("measure") if typ == "typical" else None,
            "cm_low": lo,
            "basis": typ,                      # "common" | "max" | "manual"
            "bucket": adult,                   # primary match
            "buckets_juvenile": juvenile,      # secondary, shown below a divider
            "source": ("manual" if how == "override"
                       else (tp.get("source_url") or "researched") if typ == "typical"
                       else ("SeaLifeBase " + SLB_RELEASE
                             if (rec or {}).get("_src") == SLB_RELEASE
                             else "FishBase " + FB_RELEASE)),
            "matched": how,
        }
        how_tally[how] += 1

    cache_write("size.json", out)
    pct = len(out) / max(1, len(species)) * 100
    log(f"4/6 size         {len(out):>4} of {len(species)} ({pct:.0f}%)  "
        + " ".join(f"{k}={v}" for k, v in how_tally.items() if v))
    missing = [s for s in species if str(s["taxon_id"]) not in out]
    if missing:
        top = sorted(missing, key=lambda s: -s["annual"])[:3]
        log("    no size (render grey): " + ", ".join(f"{s['name']}" for s in top)
            + (f" +{len(missing)-3}" if len(missing) > 3 else ""))
        log(f"    add any of these by hand to tools/size-overrides.json "
            f"as {{\"<scientific name>\": <max cm>}}")
    return out


# ── Stage 2b: seasonality, at the scale it is actually visible ───────────────
#
# The bay's own record cannot see a season. Measured: a species with 1-2 local
# records has an 8% chance of one landing in September — exactly the random
# expectation for one month in twelve — while a species with 100+ has 93%. That
# is a rarity gradient wearing a seasonal costume, and 67% of the list has 12
# records or fewer. Charting it would show who went diving.
#
# NSW-wide it IS visible: the median species has ~39x more records there, and
# 555 of 640 clear 24. A fish does not keep a different calendar at Shelly than
# at Bondi, so the honest split is local list, regional season.
#
# Then three states, because "has a season" is not the default:
#
#   real     the best four CONSECUTIVE months hold >=50% of the normalised
#            year, from enough records to mean it. Port Jackson Shark peaks in
#            July at 20% of its year, running AGAINST the effort curve, which
#            is what a genuine signal looks like.
#   flat     enough records, no concentration. 49%. Anemones, limpets, urchins
#            — fixed to the rock and there every day. "Here all year" is a
#            finding, not a gap.
#   unknown  too few records anywhere to say.
#
# Anything below the bar is called flat rather than "possibly seasonal": a
# marginal concentration could still be regional dive-club rhythm, water
# clarity or school holidays, and claiming a season on that is the error this
# whole stage exists to avoid.

BAY_MIN_FOR_AGREEMENT = 12   # fewer bay records than this and r is noise
SEASON_BAY_MIN = 0.30        # below this the bay does not back the NSW season
SEASON_MIN_RECORDS = 24    # ~2 a month before a shape is worth reading
# Measured against species whose behaviour is known. The busiest THREE months
# was the wrong test: it called Port Jackson Shark flat, because its winter
# aggregation spans four months (Jun-Sep at 13/20/16/12%) and top-3 caught only
# 49% of it. A season is contiguous by nature, so ask for the best four
# CONSECUTIVE months instead — and demanding contiguity is also what separates
# a real signal from scatter, since noise does not cluster in a row.
#
#   known seasonal   Port Jackson 61%  Moorish Idol 61%  Dusky Shark 80%
#   known resident   Waratah Anemone 37%  Blue Groper 40%  Luderick 43%
#
# The gap between 44% and 56% is empty, so the bar goes in the middle of it.
SEASON_STRONG = 0.50       # share of the year in the best 4 consecutive months


def stage_season(species: list, force: bool) -> dict:
    cached = None if force else cache_read("season.json")
    if cached:
        log(f"2b/6 season      {len(cached['species']):>4} (cached)")
        return cached

    months, effort = {}, [0] * 12
    for m in range(1, 13):
        seen, page = 0, 1
        while True:
            d = inat("observations/species_counts", place_id=NSW_PLACE, taxon_id=TAXA,
                     per_page=500, page=page, verifiable="true", month=m)
            for r in d["results"]:
                months.setdefault(r["taxon"]["id"], [0] * 12)[m - 1] = r["count"]
                effort[m - 1] += r["count"]
            seen += len(d["results"])
            if seen >= d["total_results"] or not d["results"]:
                break
            page += 1
        log(f"    month {m:>2}  {d['total_results']:>4} species across NSW")

    # Dividing by raw effort OVER-corrects, and the proof is in the species that
    # have no season at all. A flat species should sit at 8.3% every month; the
    # 343 of them averaged 10.0% in June and 6.4% in October, tracking the
    # inverse of effort. June has the least observing, so dividing by that small
    # denominator inflated every June share — and with it a third of the
    # seasonal verdicts, which were reading the calendar of the observers.
    #
    # So the flat species become the control. Whatever drives the residual —
    # what people go out looking for, water clarity, the mix of taxa recorded in
    # summer — it applies to them too, and dividing it out needs no theory of
    # which cause it was.
    #
    # This is mildly circular: "flat" was itself decided on the biased shares.
    # The pool is the species that stayed under the bar DESPITE the inflation,
    # which makes it a conservative baseline — a clear improvement, not an exact
    # correction.
    def normalise(m):
        share = [m[i] / effort[i] if effort[i] else 0 for i in range(12)]
        tot = sum(share) or 1
        return [x / tot for x in share]

    def read_shape(share):
        best = max(range(12), key=lambda i: sum(share[(i + j) % 12] for j in range(4)))
        return best, sum(share[(best + j) % 12] for j in range(4))

    # pass one: effort-normalised only, to find the species with no season
    first = {}
    for s in species:
        m = months.get(s["taxon_id"])
        if not m or sum(m) < SEASON_MIN_RECORDS:
            continue
        share = normalise(m)
        first[str(s["taxon_id"])] = (share, read_shape(share)[1])

    control = [sh for sh, w in first.values() if w < SEASON_STRONG]
    if len(control) >= 50:
        baseline = [sum(sh[i] for sh in control) / len(control) for i in range(12)]
        flattest = max(baseline) / max(1e-9, min(baseline))
        log(f"2b/6 season      baseline from {len(control)} season-less species; "
            f"month bias spans {flattest:.2f}x before correction")
    else:
        baseline = [1 / 12] * 12          # too few to trust; leave it alone
        log(f"2b/6 season      only {len(control)} season-less species — "
            f"correcting nothing")

    out = {}
    for s in species:
        m = months.get(s["taxon_id"])
        if not m:
            # No NSW records at all. Skipping left the field null and relied on
            # the app treating null the same as unknown, which it happens to do
            # — say it instead of depending on that.
            out[str(s["taxon_id"])] = {"state": "unknown", "records": 0}
            continue
        total = sum(m)
        if total < SEASON_MIN_RECORDS:
            out[str(s["taxon_id"])] = {"state": "unknown", "records": total}
            continue
        share = normalise(m)
        adj = [share[i] / baseline[i] if baseline[i] else 0 for i in range(12)]
        tot = sum(adj) or 1
        share = [x / tot for x in adj]
        best, window = read_shape(share)
        out[str(s["taxon_id"])] = {
            "state": "real" if window >= SEASON_STRONG else "flat",
            "records": total,
            "share": [round(x, 4) for x in share],
            "peak": share.index(max(share)),
            "concentration": round(window, 3),
            "window_from": best,
        }

    doc = {"place": NSW_PLACE, "effort": effort, "species": out}
    cache_write("season.json", doc)
    import collections
    c = collections.Counter(v["state"] for v in out.values())
    log(f"2b/6 season      {c['real']} with a real season, {c['flat']} here all year, "
        f"{c['unknown']} too few records")
    return doc


# ── Stage 4b: typical length, search-grounded ────────────────────────────────
#
# FishBase's `Length` is the largest specimen ever recorded, and only 24% of
# species carry a `CommonLength`. Measured on the 102 that have both, the max
# and the typical fall in DIFFERENT size buckets 62% of the time — median ratio
# 0.62 — and the error concentrates in big animals (85% above 80 cm).
#
# That matters because size is the axis that narrows hardest. Yellowtail
# Kingfish at its 250 cm maximum is "bigger than you"; the 80 cm fish you
# actually meet is "as long as you". Australian Bonito goes from "bigger than
# you" to "arm's length".
#
# So: look the typical length up, with a citation. Haiku does this for $0.021 a
# species against Opus's $0.222, because the cost is dominated by search results
# arriving as input tokens and this is fact extraction, not reasoning.
#
# What is NOT done here: scaling max by the 0.62 median. The ratio ranges
# 0.25-0.92, so that would be an invented number wearing the clothes of a
# measurement — exactly what the grey-not-green rule exists to stop.

SIZE_MODEL = "claude-haiku-4-5"
SIZE_PRICE_IN, SIZE_PRICE_OUT, SEARCH_PRICE = 1.00, 5.00, 0.010

TYPICAL_SYS = (
    "Find the TYPICAL adult length of the species, in centimetres: the size an "
    "ordinary healthy adult reaches, which is what a snorkeller actually meets. "
    "This is NOT the maximum ever recorded, which is usually far larger. Prefer "
    "Australian sources — Fishes of Australia, the Australian Museum, state "
    "fisheries departments, Reef Life Survey. Give the source URL you used. If "
    "no credible source states a typical size, set confident false; a missing "
    "figure is better than a guessed one.\n\n"
    "Reply with ONLY a JSON object and nothing else:\n"
    '{"typical_cm": <number or null>, "max_cm": <number or null>, '
    '"source_url": "<url or null>", "confident": <true|false>}')


def _typical_lookup(client, sci: str, common: str):
    resp = client.messages.create(
        model=SIZE_MODEL, max_tokens=2000, system=TYPICAL_SYS,
        tools=[{"type": "web_search_20260209", "name": "web_search",
                "max_uses": 2, "allowed_callers": ["direct"]}],
        messages=[{"role": "user",
                   "content": f"Typical adult length of {sci} ({common})?"}],
    )
    u = resp.usage
    searches = getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0
    cost = (u.input_tokens / 1e6 * SIZE_PRICE_IN
            + u.output_tokens / 1e6 * SIZE_PRICE_OUT + searches * SEARCH_PRICE)
    text = "".join(c.text for c in resp.content if c.type == "text")
    m = re.search(r'\{[^{}]*"typical_cm"[^{}]*\}', text, re.S)
    if not m:
        return {"status": "unparsed"}, cost
    try:
        rec = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"status": "unparsed"}, cost
    # A DECLINE and a MISS are different animals. Giant Cuttlefish declines
    # because every source states a maximum and none a typical — retrying that
    # forever is waste. Sergeant Baker declined once and answered on the retry,
    # which is variance. Recording only "confident: false" made them identical
    # and left a 33% hit rate impossible to act on.
    if rec.get("confident") and rec.get("typical_cm"):
        rec["status"] = "found"
    elif rec.get("max_cm") or rec.get("source_url"):
        rec["status"] = "declined"      # it looked, and said no honestly
    else:
        rec["status"] = "miss"          # nothing useful came back — worth a retry
    return rec, cost


def apply_typical_overrides(species: list, typical: dict) -> dict:
    """Fold hand-researched typical lengths into the typical cache.

    Keyed by SCIENTIFIC name, because that is the stable identifier — the six
    species Sear flagged as "missing" were all present under a different
    COMMON name, and two under a renamed genus. Never key size on a common name.

    These land in the same slot as a model lookup, so stage_size keeps using
    the FishBase maximum for the "grows to" figure and only the bucket moves.
    An entry marked estimated=true carries no source that states a typical
    size; the app must word those as an estimate, per the rule that a model
    must never visually outrank a real observation.
    """
    path = ROOT / "tools" / "typical-overrides.json"
    if not path.exists():
        return typical
    ov = {k: v for k, v in json.loads(path.read_text("utf-8")).items()
          if not k.startswith("_") and isinstance(v, dict) and v.get("typical_cm")}
    by_sci = {s["sci"]: str(s["taxon_id"]) for s in species}
    used, unknown, absurd = 0, [], []
    for sci, v in ov.items():
        tid = by_sci.get(sci)
        if tid is None:
            unknown.append(sci)
            continue
        # A typical length above the recorded maximum means one of the two is
        # about a different animal. Refuse it rather than average the error in.
        #
        # But only when the two measure the SAME thing. A 17.5 cm carapace and
        # a 13 cm "max" are not in conflict — they are different rulers, and
        # comparing them threw out four good answers. Anything not measured as
        # total length is passed through, since the held maximum always is.
        held = (typical.get(tid) or {}).get("max_cm")
        measure = v.get("measure") or "total length"
        if measure == "total length" and held and float(v["typical_cm"]) > float(held) * 1.05:
            absurd.append(f"{sci} ({v['typical_cm']}cm typical > {held}cm max)")
            continue
        rec = dict(typical.get(tid) or {})
        rec.update({"typical_cm": float(v["typical_cm"]), "status": "found",
                    "confident": True, "source_url": v.get("source") or "researched",
                    "estimated": bool(v.get("estimated")),
                    "measure": v.get("measure") or "total length"})
        typical[tid] = rec
        used += 1
    log(f"4c/6 typical ov  {used} applied from tools/typical-overrides.json"
        + (f" · {sum(1 for k in ov.values() if k.get('estimated'))} are estimates" if used else ""))
    for label, items in (("not in the species list", unknown),
                         ("rejected, above the recorded maximum", absurd)):
        if items:
            log(f"     {len(items)} {label}: " + ", ".join(items[:4])
                + (" ..." if len(items) > 4 else ""))
    return typical

def stage_typical(species: list, sizes: dict, force: bool, limit: int = 0,
                  min_cm: float = 0.0, retry: bool = True) -> dict:
    """Look up typical length where it will actually change a size bucket.

    The 40cm floor this used to carry was a mistake. It was reasoned from
    BUCKET error, which does concentrate in big animals — but it left 288
    species never looked up at all, every one of them still printing a
    maximum. Southern Maori Wrasse sat at exactly 40cm with 261 bay records,
    excluded by a > that should have been >=. At $0.021 a species there is no
    reason to be clever about which ones deserve checking.
    """
    cache = {} if force else (cache_read("typical.json") or {})
    # Entries written before the status field existed still hold a good answer;
    # requiring status=="found" silently discarded them — including the Eastern
    # Blue Groper lookup that started this whole thread.
    for v in cache.values():
        if "status" not in v:
            v["status"] = "found" if (v.get("confident") and v.get("typical_cm"))                           else "declined"

    def wants(s):
        sz = sizes.get(str(s["taxon_id"])) or {}
        if sz.get("basis") == "common":
            return False                      # FishBase already has a typical
        cm = sz.get("cm")
        return cm is None or cm > min_cm      # unknown, or big enough to matter

    def pending(s):
        got = cache.get(str(s["taxon_id"]))
        if got is None:
            return True
        # retry transient misses; never re-ask something that declined honestly
        return retry and got.get("status") in ("miss", "unparsed")

    todo = [s for s in species if wants(s) and pending(s)]
    if limit:
        todo = sorted(todo, key=lambda s: -s["annual"])[:limit]
    if not todo:
        log(f"4b/6 typical     {len(cache):>4} (cached)")
        return cache

    _require_key("4b/6 typical")
    est = len(todo) * 0.021
    log(f"4b/6 typical     {len(todo)} to look up on {SIZE_MODEL} (~${est:.2f})")
    client = anthropic.Anthropic()
    spent, done, found = 0.0, 0, 0

    def look(s):
        try:
            return str(s["taxon_id"]), _typical_lookup(client, s["sci"], s["name"])
        except Exception as e:                        # noqa: BLE001
            log(f"    lookup failed {s['name']}: {type(e).__name__}")
            return str(s["taxon_id"]), (None, 0.0)

    with ThreadPoolExecutor(max_workers=TAG_WORKERS) as pool:
        for fut in as_completed([pool.submit(look, s) for s in todo]):
            tid, (rec, cost) = fut.result()
            spent += cost
            done += 1
            if rec and rec.get("status") == "found":
                cache[tid] = rec
                found += 1
            elif rec:
                cache[tid] = rec                      # keeps status for retries
            else:
                cache[tid] = {"confident": False, "status": "error"}
            if done % 25 == 0:
                log(f"      {done}/{len(todo)}  ${spent:.2f}")
                cache_write("typical.json", cache)
    cache_write("typical.json", cache)
    import collections
    st = collections.Counter(v.get("status", "?") for v in cache.values())
    log(f"4b/6 typical     {found} found of {len(todo)} tried · ${spent:.2f} actual")
    log(f"    cache now: " + ", ".join(f"{v} {k}" for k, v in st.most_common()))
    return cache


# ── Stage 5: the vision pass ─────────────────────────────────────────────────

def tag_one(client, name: str, img_path: Path) -> Optional[dict]:
    b64 = base64.standard_b64encode(img_path.read_bytes()).decode()
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=2000,
        system=TAG_SYSTEM,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/webp", "data": b64}},
                {"type": "text",
                 "text": f"This is a {name}, photographed in Cabbage Tree Bay. "
                         f"Tag what you can see."},
            ],
        }],
        output_format=FishTags,
    )
    SPEND.add(resp.usage)
    parsed = resp.parsed_output
    return parsed.model_dump() if parsed else None


def report_distribution(tags: dict, sizes: dict, species: list) -> None:
    """Does the vocabulary actually partition the bay?

    A facet earns its place by splitting the list. If one value holds most of
    the species it narrows nothing, and if a value holds none it is dead UI.
    Run this on a sample BEFORE committing to all 427 — changing the vocabulary
    afterwards means re-tagging and, worse, throwing away the human review.
    """
    import collections
    n = len(tags)
    if not n:
        log("  no tags yet — nothing to profile")
        return

    axes = {
        "shape": [t.get("shape") for t in tags.values()],
        "colour": [t.get("colour_primary") for t in tags.values()],
        "where": [t.get("where") for t in tags.values()],
        "finish": [t.get("finish") for t in tags.values()],
        "tail": [t.get("tail_shape") for t in tags.values()],
        "markings": [m for t in tags.values() for m in (t.get("markings") or [])],
    }
    by_taxon = {str(s["taxon_id"]): s for s in species}
    axes["size"] = [sizes.get(tid, {}).get("bucket") for tid in tags]

    print()
    log(f"  VOCABULARY PROFILE over {n} tagged species")
    log("  'typical' is the average share left after one pick; 'best' is the")
    log("  narrowest single pick. A 2-value axis looks bad by 'typical' and can")
    log("  still be a fine filter — finish is 82/18, and tapping the 18 is good.")
    for axis, vals in axes.items():
        c = collections.Counter(v for v in vals if v)
        if not c:
            print(f"    {axis:9s} — empty")
            continue
        top, topn = c.most_common(1)[0]
        filled = sum(c.values())

        # "Top value is big" is the WRONG test on a two-value axis: finish runs
        # 82% matte / 18% silvery, and tapping "silvery" is an excellent cut
        # precisely because the other value is huge. So report what a swimmer
        # actually experiences instead of flagging the mode:
        #
        #   typical  — expected share left after one pick (sum of squared
        #              shares). This is the honest average-case number.
        #   best     — the narrowest single pick available on this axis.
        typical = sum((v / filled) ** 2 for v in c.values())
        best = min(c.values()) / filled
        flag = "  <-- narrows little even at best" if best > 0.35 else ""
        print(f"    {axis:9s} {len(c)} values · typical pick leaves "
              f"{typical*100:.0f}% · best pick leaves {best*100:.0f}%{flag}")
        print("              " + ", ".join(f"{k}:{v}" for k, v in c.most_common(6)))

    conf = collections.Counter(t.get("confidence") for t in tags.values())
    print(f"    {'confidence':9s} " + ", ".join(f"{k}:{v}" for k, v in conf.most_common()))

    # How many of the tagged species left each axis EMPTY. This is the number
    # that decides whether a facet can be a filter chip at all: an axis that is
    # null for most species excludes almost everything the moment it is used.
    print()
    log("  COVERAGE — an axis mostly null cannot be a filter")
    for axis, vals in axes.items():
        if axis == "markings":
            continue                       # a list, not a single value
        filled = sum(1 for v in vals if v)
        flag = "  <-- too sparse to filter on" if filled / n < 0.5 else ""
        print(f"    {axis:9s} {filled}/{n} filled ({filled/n*100:.0f}%){flag}")


def _stale_against_schema(tag: dict) -> Optional[str]:
    """Is this cached tag still expressible in the CURRENT vocabulary?

    Every enum change used to mean re-tagging all 600 species, which is the
    thing that made changing one's mind expensive. It doesn't have to: a tag
    only goes stale if it holds a value the enum no longer offers. Splitting
    "snail-or-shell" into "shelled-snail" and "sea-slug" invalidates the 80
    species that carried it and nothing else — about $1.70 rather than $13.

    Valid values are read off the Pydantic model, so this cannot drift out of
    step with the schema the way a hand-maintained list would.
    """
    import typing
    for field, info in FishTags.model_fields.items():
        val = tag.get(field)
        if val is None:
            continue
        ann = info.annotation
        # unwrap Optional[...] / List[...] to the Literal underneath
        args = typing.get_args(ann)
        lit = ann
        for a in args:
            if typing.get_origin(a) is typing.Literal or hasattr(a, "__args__"):
                lit = a
        allowed = getattr(lit, "__args__", None)
        if not allowed or not all(isinstance(x, str) for x in allowed):
            continue
        vals = val if isinstance(val, list) else [val]
        for v in vals:
            if isinstance(v, str) and v not in allowed:
                return f"{field}={v}"
    return None


def stage_tags(species: list, photos: dict, force: bool, sample: int = 0, picks: dict = None) -> dict:
    tags = {} if force else (cache_read("tags.json") or {})

    # A shape asked by NAME outranks anything read off a photograph, so it must
    # survive a re-tag. tags[tid] = t replaces the whole record, which would
    # quietly hand 640 species' shapes back to the picture they were taken off
    # — the better-sourced value losing to the weaker one, silently, for money.
    # kept_shape is a belt over braces now: name-shapes.json is re-applied on
    # every run regardless of what happens to a tag record. Left in place so a
    # crash mid-stage cannot show a photo-derived shape at the next emit.
    kept_shape = {tid: {k: v for k, v in (t or {}).items()
                        if k in ("shape", "shape_from", "shape_was_photo")}
                  for tid, t in tags.items() if (t or {}).get("shape_from")}

    # Drop anything the current vocabulary can no longer express, so an enum
    # change re-tags exactly the species it affects and no others.
    #
    # And drop anything whose PHOTOGRAPH has changed. A tag is a description of
    # a particular image — colour, markings, finish, tail all come from looking
    # at it — so when the picture is replaced the description is of something
    # the reader can no longer see. Widening the photo pool from the bay to NSW
    # changed 504 pictures at once, and without this every one of them would
    # have kept a caption written about the shot it replaced.
    #
    # kept_shape is captured above, before any of this, so a shape asked by
    # name is not thrown away with the photo description that surrounds it.
    import collections
    stale = collections.Counter()
    for tid in list(tags):
        why = _stale_against_schema(tags[tid] or {})
        if not why:
            # Stale means the tagged photo is GONE from the candidate list, not
            # that it is no longer first. stage_tags walks the candidates until
            # one is usable, so ~50 species are legitimately tagged from the
            # second or third — comparing against [0] alone called every one of
            # them replaced, dropped them, and (with no key in that shell) left
            # them with no tags and no shape at all.
            was = ((tags[tid] or {}).get("photo") or {}).get("photo_id")
            cl = photos.get(tid) or []
            current = {c.get("photo_id") for c in cl}
            shown = (picks or {}).get(tid, {}).get("photo_id") or (
                cl[0].get("photo_id") if cl else None)
            if was is not None and current and was not in current:
                why = "photo replaced"
            elif was is not None and shown is not None and was != shown:
                # The description belongs to a photograph nobody will see now.
                why = "a different photo chosen"
        if why:
            stale[why] += 1
            del tags[tid]
    if stale:
        log("    retired vocabulary, re-tagging: "
            + ", ".join(f"{v}x {k}" for k, v in stale.most_common(5)))
        # Write the shrunken cache NOW, as the new baseline.
        #
        # The guard in cache_write refuses a write that loses half the entries,
        # because that is what an accidental subset looks like. A deliberate
        # re-tag looks identical to it: dropping 493 stale records left 131, and
        # the first checkpoint tried to write 151 over 624 and was refused. The
        # run then kept going — a thread pool drains before the exception
        # surfaces — so every one of 509 calls was made and every one was
        # thrown away.
        #
        # The deletion above is explicit, counted and logged. Recording it
        # immediately makes it the thing later checkpoints are measured
        # against, so they grow rather than shrink and the guard still catches
        # the accident it was written for.
        cache_write("tags.json", tags, allow_shrink=True)
    todo = [s for s in species
            if str(s["taxon_id"]) in photos and str(s["taxon_id"]) not in tags]

    if sample and todo:
        # Stratified by abundance: the common fish a swimmer actually meets,
        # plus some of the long tail, so the profile isn't all charismatic reef
        # species. Deterministic, so a repeat run reuses the same cache.
        todo.sort(key=lambda s: -s["annual"])
        step = max(1, len(todo) // sample)
        todo = todo[::step][:sample]
        log(f"    --sample {sample}: stratified across the abundance range")

    if not todo:
        log(f"5/6 tags         {len(tags):>4} (all cached)")
        return tags

    # Pre-flight estimate, so nobody discovers the bill after the fact.
    # These are MEASURED over a 40-species sample, not reasoned from the rate
    # card. My a-priori guess was 910 in / 500 out and the input was out by 3x:
    # the injected JSON schema costs far more than the prompt text does.
    est_in, est_out = 2_708, 415
    est = len(todo) * (est_in / 1e6 * PRICE_IN + est_out / 1e6 * PRICE_OUT)
    _require_key("5/6 tags")
    log(f"5/6 tags         {len(todo)} to tag ({len(tags)} already done)")
    log(f"    estimated ~${est:.2f} on {MODEL} — actual cost reported at the end")
    client = anthropic.Anthropic()
    done = 0

    def work(s):
        tid = str(s["taxon_id"])
        fallback = None
        # Walk the candidate photos until one is good enough to describe.
        for cand in photos[tid]:
            img = OUT_DIR / cand["file"]
            if not img.exists() and not download_square(cand["url"], img):
                continue
            # One bad image must never kill a 400-species run. A TLS handshake
            # timeout did exactly that at species 200 of 417, because only
            # APIStatusError was caught and APITimeoutError is a sibling, not a
            # subclass. Catch the whole family, plus anything else, and skip the
            # species: it stays out of the cache, so a re-run picks it up.
            try:
                # Retry like every other paid stage. 194 calls were lost to
                # transient HTTP 400s in one run with no second attempt, and a
                # skipped species is a species with no colour at all.
                t = None
                for attempt in (1, 2, 3):
                    try:
                        t = tag_one(client, s["name"], img)
                        break
                    except anthropic.APIStatusError as e:
                        if attempt == 3 or e.status_code not in (400, 429, 500, 502, 503, 529):
                            raise
                        time.sleep(1.5 * attempt)
            except anthropic.APIStatusError as e:
                log(f"    HTTP {e.status_code} on {s['name']} — skipped")
                return tid, None
            except anthropic.APIError as e:
                log(f"    {type(e).__name__} on {s['name']} — skipped, will retry next run")
                return tid, None
            except Exception as e:                    # noqa: BLE001
                log(f"    unexpected {type(e).__name__} on {s['name']}: {e}")
                return tid, None
            if t and t.get("photo_usable"):
                t["photo"] = cand
                return tid, t
            if t and fallback is None:
                fallback = (t, cand)      # keep the first rejected read

        # Every candidate was rejected as an ID reference. Dropping the species
        # would be worse than describing it badly: the swimmer who saw it would
        # find nothing at all. So keep the best rejected read, force confidence
        # to low so it lands in the review file, and mark why.
        if fallback:
            t, cand = fallback
            t["photo"] = cand
            t["confidence"] = "low"
            t["photo_caveat"] = "no candidate photo shows the whole animal"
            return tid, t
        return tid, None

    with ThreadPoolExecutor(max_workers=TAG_WORKERS) as pool:
        futures = {pool.submit(work, s): s for s in todo}
        for fut in as_completed(futures):
            tid, t = fut.result()
            done += 1
            if t:
                tags[tid] = t
                # restore per result, not at the end of the stage. The
                # end-of-stage restore never runs if the stage dies, and
                # the checkpoint would then hold a shape taken from the
                # photograph — quietly undoing the name pass for exactly
                # the species that had just been re-tagged.
                if tid in kept_shape:
                    tags[tid].update(kept_shape[tid])
            if done % 20 == 0:
                log(f"    {done}/{len(todo)} tagged")
                cache_write("tags.json", tags)      # checkpoint; API calls cost money

    for tid, keep in kept_shape.items():
        if tid in tags:
            tags[tid].update(keep)
    cache_write("tags.json", tags)
    log(f"5/6 tags         {len(tags):>4} tagged")
    print(SPEND.report())
    return tags


# ── Stage 5b: settle the shape by vote ───────────────────────────────────────
#
# Measured: re-reading the SAME photo gives a different shape 24% of the time.
# One read is an opinion, not a fact — and shape is the primary axis, the
# silhouette row at the top of the picker, so a wrong one makes the animal
# unfindable however good the rest of the tag is.
#
# A ballot asks one question with one enum: 786 input tokens against the full
# call's 2,542, so ~$0.005 each against $0.023. And we only cast where a species
# is unsettled. Naive best-of-three over everything is ~$42; this is under $2.
#
# What it buys is STABILITY, not truth. Three reads of an ambiguous photo can
# agree on the same wrong answer. It removes noise, not bias.

VOTE_SYSTEM = (
    "Name the body silhouette of the animal in this photograph, choosing only "
    "from the supplied list. Judge the whole body outline, not the head or any "
    "single feature. If you cannot see enough of the animal to judge its shape, "
    "set photo_usable false rather than guessing. Report only what is visible.")


class ShapeVote(BaseModel):
    photo_usable: bool
    shape: Optional[Shape] = None


def cast_ballot(client, name: str, img_path: Path) -> Optional[str]:
    b64 = base64.standard_b64encode(img_path.read_bytes()).decode()
    resp = client.messages.parse(
        model=MODEL, max_tokens=500, system=VOTE_SYSTEM,
        messages=[{"role": "user", "content": [
            {"type": "image",
             "source": {"type": "base64", "media_type": "image/webp", "data": b64}},
            {"type": "text", "text": f"This is a {name}. Which shape is it?"}]}],
        output_format=ShapeVote)
    SPEND.add(resp.usage)
    out = resp.parsed_output
    return out.shape if (out and out.photo_usable) else None


def _votes_of(t: dict) -> list:
    """Vote history, filtered to shapes the CURRENT enum still offers.

    Votes outlive vocabulary changes, and a retired value sitting in the history
    manufactures a disagreement that never happened: three of the five species
    reported as "contested" were really one live shape against the pre-split
    `snail-or-shell`. The staleness check guards the `shape` field; it has to
    guard the ballot record too.
    """
    valid = set(Shape.__args__)
    votes = [v for v in (t.get("shape_votes") or []) if v in valid]
    if not votes and t.get("shape") in valid:
        votes = [t["shape"]]
    return votes


def _settled(v: list) -> bool:
    return (len(v) >= 2 and len(set(v)) == 1) or len(v) >= 3


def stage_vote(species: list, tags: dict) -> dict:
    """Cast ballots until each species has two agreeing reads, or three total."""
    import collections
    client = anthropic.Anthropic()

    for rnd in (1, 2):
        todo = [s for s in species
                if str(s["taxon_id"]) in tags
                and tags[str(s["taxon_id"])].get("shape")
                and not _settled(_votes_of(tags[str(s["taxon_id"])]))]
        if not todo:
            break
        log(f"    ballot round {rnd}: {len(todo)} species unsettled")

        def ballot(s):
            tid = str(s["taxon_id"])
            img = OUT_DIR / (tags[tid].get("photo") or {}).get("file", "")
            if not img.exists():
                return tid, None
            try:
                return tid, cast_ballot(client, s["name"], img)
            except Exception as e:                        # noqa: BLE001
                log(f"    ballot failed on {s['name']}: {type(e).__name__}")
                return tid, None

        done = 0
        with ThreadPoolExecutor(max_workers=TAG_WORKERS) as pool:
            for fut in as_completed([pool.submit(ballot, s) for s in todo]):
                tid, shape = fut.result()
                done += 1
                if shape:
                    tags[tid]["shape_votes"] = _votes_of(tags[tid]) + [shape]
                if done % 40 == 0:
                    log(f"      {done}/{len(todo)}")
                    cache_write("tags.json", tags)
        cache_write("tags.json", tags)

    agreed = contested = 0
    for s in species:
        t = tags.get(str(s["taxon_id"]))
        if not t or not t.get("shape"):
            continue
        v = _votes_of(t)
        top, n = collections.Counter(v).most_common(1)[0]
        t["shape"], t["shape_votes"] = top, v
        if n >= 2:
            agreed += 1
            t.pop("shape_contested", None)
        else:
            contested += 1
            t["shape_contested"] = True     # genuinely "the machine can't tell"
    cache_write("tags.json", tags)
    log(f"5b/6 vote        {agreed} settled by majority, {contested} still contested")
    print(SPEND.report())
    return tags


# ── Stage 5c: re-read colour on white-balanced photos ────────────────────────
#
# Water absorbs red first, so an animal photographed in ambient light can look
# nothing like itself. Coffin Ray is a sandy brown animal and was tagged
# grey/purple — a faithful description of the photograph and a wrong one of the
# fish. Measured over 120 photos: the median cast is near neutral because many
# used flash, but 34% have their red badly suppressed.
#
# So correct those, and ask again. Two deliberate limits:
#
#   * The correction is BOUNDED grey-world. Grey-world assumes the average
#     scene is neutral, which holds for a frame full of sand and fails badly
#     for one full of open water — uncapped, that scene goes orange and we
#     would have traded one wrong colour for another.
#   * It runs on a temporary copy. The photo we display stays the
#     photographer's original: we are correcting our own reading of it, not
#     republishing an altered version of someone's work.

# TRIED AND REVERTED, 3 Sep 2026. Correcting 195 photos and re-reading their
# colour changed 77 hues for $1.07, and made the data worse:
#
#   Silver Trevally  cast 0.48  silver -> LURID PINK
#   Yellowfin Leatherjacket 0.67  cream -> pink
#   Luderick         cast 0.76  green -> grey        (a fair improvement)
#
# Grey-world assumes the average scene is neutral. Underwater the scene really
# is green-blue, so the correction pours red in, and the SUBJECT — correctly
# silver — comes out pink. 17 of the 77 changes went to pink, which is the
# signature. It fails hardest on the strongest casts, i.e. exactly the photos
# worth correcting, and the 1.75 gain cap was nowhere near tight enough.
#
# It also never touched the Coffin Ray that prompted it: a pale animal keeps
# its channel means up, so the cast ratio never crossed the threshold.
#
# If revisited: correct only mild casts (>=0.70, where gains are small), or
# segment the subject from the water first and balance on the subject alone.
# The blunt whole-frame version is a dead end — don't pay for it twice.
CAST_THRESHOLD = 0.85      # red/green below this = red badly suppressed
WB_GAIN_CAP = 1.75         # never push a channel further than this


def _channel_means(im):
    small = im.convert("RGB").resize((64, 64))
    px = list(small.getdata())
    n = len(px)
    return [sum(p[i] for p in px) / n for i in range(3)]


def _cast_ratio(im) -> float:
    m = _channel_means(im)
    return m[0] / max(1.0, m[1])          # red relative to green


def _white_balance(im):
    """Bounded grey-world. The cap is the whole safety story: uncapped, a frame
    of open blue water gets its red multiplied into orange, and we would have
    swapped one wrong colour for another."""
    im = im.convert("RGB")
    m = _channel_means(im)
    grey = sum(m) / 3
    gains = [min(WB_GAIN_CAP, max(1 / WB_GAIN_CAP, grey / max(1.0, c))) for c in m]
    lut = []
    for g in gains:
        lut += [min(255, int(i * g)) for i in range(256)]
    return im.point(lut)


COLOUR_SYS = (
    "Name the dominant hue of the animal in this photograph, choosing only from "
    "the supplied list. The image has been white-balance corrected, so judge the "
    "colours as you see them now. Silver is not a hue: a silvery fish is usually "
    "grey or white. If the animal is too obscured to judge colour, set "
    "photo_usable false rather than guessing.")


class ColourVote(BaseModel):
    photo_usable: bool
    colour_primary: Optional[Colour] = None
    colour_secondary: Optional[Colour] = None


def stage_colour(species: list, tags: dict, force: bool = False) -> dict:
    from PIL import Image as _Im
    client = anthropic.Anthropic()
    wb_dir = CACHE / "wb"
    wb_dir.mkdir(parents=True, exist_ok=True)

    todo = []
    for s in species:
        tid = str(s["taxon_id"])
        t = tags.get(tid)
        if not t or not t.get("colour_primary"):
            continue
        if t.get("colour_wb") and not force:
            continue
        f = (t.get("photo") or {}).get("file")
        if not f or not (OUT_DIR / f).exists():
            continue
        try:
            if _cast_ratio(_Im.open(OUT_DIR / f)) < CAST_THRESHOLD:
                todo.append((s, OUT_DIR / f))
        except Exception:                                  # noqa: BLE001
            continue

    if not todo:
        log("5c/6 colour      nothing badly cast, or all already re-read")
        return tags

    log(f"5c/6 colour      {len(todo)} photos with suppressed red "
        f"({len(todo)/max(1,len(species))*100:.0f}% of the list)")

    def ballot(pair):
        s, path = pair
        tid = str(s["taxon_id"])
        try:
            im = _white_balance(_Im.open(path).convert("RGB"))
            tmp = wb_dir / (path.stem + ".webp")
            im.save(tmp, "WEBP", quality=80)
            b64 = base64.standard_b64encode(tmp.read_bytes()).decode()
            resp = client.messages.parse(
                model=MODEL, max_tokens=500, system=COLOUR_SYS,
                messages=[{"role": "user", "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/webp", "data": b64}},
                    {"type": "text", "text": f"This is a {s['name']}. What colour is it?"}]}],
                output_format=ColourVote)
            SPEND.add(resp.usage)
            return tid, resp.parsed_output
        except Exception as e:                             # noqa: BLE001
            log(f"    colour ballot failed on {s['name']}: {type(e).__name__}")
            return tid, None

    done = changed = 0
    with ThreadPoolExecutor(max_workers=TAG_WORKERS) as pool:
        for fut in as_completed([pool.submit(ballot, p) for p in todo]):
            tid, out = fut.result()
            done += 1
            if out and out.photo_usable and out.colour_primary:
                t = tags[tid]
                t["colour_was"] = t.get("colour_primary")
                if out.colour_primary != t.get("colour_primary"):
                    changed += 1
                t["colour_primary"] = out.colour_primary
                if out.colour_secondary:
                    t["colour_secondary"] = out.colour_secondary
                t["colour_wb"] = True          # read off a corrected image
            if done % 25 == 0:
                log(f"      {done}/{len(todo)}")
                cache_write("tags.json", tags)
    cache_write("tags.json", tags)
    log(f"5c/6 colour      {changed} of {done} changed hue after correction")
    print(SPEND.report())
    return tags


# ── Stage 5d: does the photo show the animal it is filed under? ──────────────
#
# Nothing checked this. The vision pass is told "this is a Stylocheilus
# longicauda, describe it" and obediently describes the crab in the frame; it
# is never invited to say "that is not one". iNat observations can be
# misidentified, and the photo can show the substrate or a passenger rather
# than the subject. research-grade reduces it, it does not remove it.
#
# The existing guards caught that case only by luck — the photo was also poor,
# so it came out low confidence with a caveat. A CLEAR photo of the wrong
# animal, confidently tagged, is invisible to every check here.
#
# Deliberately narrow: a crab against a sea hare is a gross mismatch any vision
# model spots. Two similar wrasses are not, and asking would trade a real
# problem for a queue full of false accusations — so the prompt says default to
# plausible whenever unsure.

PLAUSIBLE_SYS = (
    "You are checking whether a photograph shows the KIND of animal it is filed "
    "under — not identifying it to species.\n\n"
    "Answer plausible = false ONLY when the photo clearly shows a different kind "
    "of creature: a crab where a sea slug is claimed, a fish where a coral is "
    "claimed, an empty rock where an animal is claimed. When it is false, say "
    "briefly what the photo actually shows.\n\n"
    "Answer plausible = true whenever the photo is consistent with the stated "
    "animal, and ALSO whenever you are unsure — a murky or partial photo of "
    "something that could be the right animal is plausible. Do not adjudicate "
    "between similar species; you are catching filing errors, not refereeing "
    "identifications. A wrong accusation costs a person real time, so when in "
    "doubt, say plausible.")


class PhotoCheck(BaseModel):
    plausible: bool
    shows_instead: Optional[str] = Field(
        default=None,
        description="If not plausible, what the photograph actually shows, "
                    "in a few words.")


def stage_plausible(species: list, tags: dict, force: bool = False) -> dict:
    client = anthropic.Anthropic()
    todo = []
    for s in species:
        tid = str(s["taxon_id"])
        t = tags.get(tid)
        if not t or (t.get("photo_ok") is not None and not force):
            continue
        f = (t.get("photo") or {}).get("file")
        if f and (OUT_DIR / f).exists():
            todo.append((s, OUT_DIR / f))
    if not todo:
        log(f"5d/6 photo check  nothing to check")
        return tags

    log(f"5d/6 photo check  {len(todo)} photos (~${len(todo)*0.0055:.2f})")

    def check(pair):
        s, path = pair
        tid = str(s["taxon_id"])
        label = s["name"] if s["name"] != s["sci"] else s["sci"]
        try:
            b64 = base64.standard_b64encode(path.read_bytes()).decode()
            resp = client.messages.parse(
                model=MODEL, max_tokens=500, system=PLAUSIBLE_SYS,
                messages=[{"role": "user", "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/webp", "data": b64}},
                    {"type": "text",
                     "text": f"This photograph is filed as {label} ({s['sci']}). "
                             f"Does it plausibly show that kind of animal?"}]}],
                output_format=PhotoCheck)
            SPEND.add(resp.usage)
            return tid, resp.parsed_output
        except Exception as e:                             # noqa: BLE001
            log(f"    check failed on {s['name']}: {type(e).__name__}")
            return tid, None

    done = flagged = 0
    with ThreadPoolExecutor(max_workers=TAG_WORKERS) as pool:
        for fut in as_completed([pool.submit(check, p) for p in todo]):
            tid, out = fut.result()
            done += 1
            if out:
                tags[tid]["photo_ok"] = bool(out.plausible)
                if not out.plausible:
                    flagged += 1
                    tags[tid]["photo_shows"] = out.shows_instead
            if done % 40 == 0:
                log(f"      {done}/{len(todo)}  {flagged} flagged")
                cache_write("tags.json", tags)
    cache_write("tags.json", tags)
    log(f"5d/6 photo check  {flagged} of {done} photos look like a different animal")
    print(SPEND.report())
    return tags


# ── Stage 5e: shape from the species, where the photograph failed ────────────
#
# The vision prompt forbids importing remembered facts, which is right for
# DESCRIBING a photograph — it stops invented detail. But when every candidate
# photo is unusable, that rule left the species with no shape at all and pushed
# it to a human, which is absurd for an animal whose body plan is not in doubt.
# Nobody needs to be asked what shape a sea cucumber is.
#
# So: for those species only, ask by NAME and not from an image. This is a
# knowledge question with a settled answer for any well-described body plan,
# and the model is told to decline where the group genuinely varies.

SHAPE_BY_NAME_SYS = (
    "Give the body silhouette of the named marine species, choosing only from "
    "the supplied list. You are answering from what is known about the animal, "
    "not from a photograph.\n\n"
    "Most marine groups have a settled body plan: a sea cucumber is a soft "
    "elongate sausage, a barnacle is fixed to the rock, a sea star is radial. "
    "Answer confidently in those cases.\n\n"
    "Set confident false when the group genuinely varies in body plan and the "
    "species is not one you know, rather than guessing from the family.")


def _require_key(stage: str):
    """Fail before spending, and before a whole stage reports a false success.

    Without this the run made one API call per species, caught each auth
    failure as an unexplained TypeError, resolved nothing, and still printed a
    coverage table and "Done" at the end. A stage that resolved zero of 583
    must not exit 0.
    """
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise SystemExit(
            f"{stage} needs ANTHROPIC_API_KEY in the environment and it is not "
            f"set. Nothing was called and nothing was charged. "
            f"Export the key and re-run.")


# A size makes a species findable; a fact is what makes a swimmer glad they
# looked it up. The audience is people who have swum past the same animals for
# years, so the bar is "something a regular does not already know" — not an
# encyclopaedia opening line. "Eats crustaceans" is true of half this list and
# interesting about none of it.
#
# The stage is built to return NOTHING most of the time. The typical-size
# lookup declined 214 times out of 501 and that is why its numbers can be
# trusted; the same has to be true here. A fact per species would be a tell
# that the model is filling space.

# Measured, not assumed. The cheap model was tried first and 40 species were
# put through both generators with the same refute prompt: Haiku held 8 and
# lost 32, Opus held 35 and lost 5. Twenty percent against eighty-eight. The
# failure was not randomness either — Haiku reached for invented male
# nest-guarding again and again, for broadcast spawners that have never built
# a nest. At $0.029 a species all-in there is no saving worth that error rate.
FACT_MODEL = MODEL
FACT_REFUTE_MODEL = MODEL          # the stronger model marks the popular ones
# Every fact, not the most-seen 150. The generating model was told most species
# have nothing worth saying and returned a fact for all of them anyway, so its
# output carries no self-filtering: an unchecked claim is simply an unchecked
# claim, whether the species is seen 450 times a year or four. Checking the
# popular ones only would have shipped ~450 unverified facts on the strength of
# an assumption the smoke test disproved.
FACT_REFUTE_TOP = 10_000

FactCategory = Literal[
    "sex-change", "night-behaviour", "name-origin", "venom-or-spines",
    "lifespan", "breeding", "feeding-oddity", "camouflage", "movement", "other"]


class SpeciesFact(BaseModel):
    has_fact: bool
    fact: Optional[str] = Field(None, max_length=180)
    category: Optional[FactCategory] = None


class FactVerdict(BaseModel):
    true_of_this_species: bool
    # No max_length here. Capping it in the schema does not make the model
    # brief — it makes a long answer fail validation and throw away a check
    # that was already paid for, which is how 24% of the first refute run
    # died. Brevity is asked for in the prompt; max_tokens is what actually
    # bounds the response.
    reason: Optional[str] = None


FACT_SYS = (
    "You write one-line curiosities about marine animals for people who swim "
    "the same ocean bay every morning and already recognise most of what they "
    "pass.\n\n"
    "Return has_fact=false unless you are confident of something genuinely "
    "notable about THIS species. Most species do not have one, and a blank is "
    "a good answer. Do not stretch a family-level trait to fill the gap: if "
    "the fact is equally true of every wrasse, it is not a fact about this "
    "wrasse.\n\n"
    "What counts as notable, roughly in order:\n"
    "- the colours a swimmer recognises are sexes or life stages of one animal\n"
    "- what it does at night, when nobody is watching it\n"
    "- where its name came from, when the name is odd\n"
    "- venom, spines or a defence, stated as plain fact\n"
    "- a lifespan far longer or shorter than it looks\n"
    "- breeding, nest-guarding or courtship a swimmer might witness\n"
    "- a feeding or movement habit that is strange rather than merely typical\n\n"
    "Rules for the sentence: one sentence, under 140 characters, present "
    "tense, no species name (the reader is looking at it), no adjectives of "
    "praise like remarkable or fascinating, and NO advice — never tell the "
    "reader to look for it, avoid it, or do anything at all. State the fact "
    "and stop.")

FACT_REFUTE_SYS = (
    "You are checking a claim about a marine species for factual error. Assume "
    "it is wrong and try to show that.\n\n"
    "Set true_of_this_species=false if the claim is false, if it is actually "
    "true of a different species or only of the wider family, or if you cannot "
    "confirm it. Uncertainty is a refutation: a claim that survives only "
    "because nobody can check it should not survive.\n"
    "Set it true only if you positively know the claim holds for this species.\n\n"
    "Keep reason under 300 characters. One or two sentences, no preamble.")


def stage_facts(species: list, force: bool, refute: bool = True) -> dict:
    """One curiosity per species where one honestly exists, then an adversarial
    second opinion on the ones that will be read most."""
    cache = {} if force else (cache_read("facts.json") or {})
    # An error is not an answer. Caching it as though it were meant 137 species
    # — 21% of the run, Port Jackson Shark, Luderick and Moorish Idol among
    # them — were written off as "no fact" on a transient failure and never
    # asked again. A decline is permanent; an error is a retry.
    todo = [s for s in species
            if (cache.get(str(s["taxon_id"])) or {}).get("status", "missing")
            in ("missing", "error")]
    if not todo:
        log(f"5f/6 facts       {len(cache):>4} (cached)")
    else:
        _require_key("5f/6 facts")
        log(f"5f/6 facts       {len(todo)} on {FACT_MODEL} (~${len(todo)*0.0086:.2f})")
        client = anthropic.Anthropic()

        def ask(s):
            tid = str(s["taxon_id"])
            # Same intermittent malformed structured output the refute pass
            # hits, and it was costing more here: one attempt each meant a
            # fifth of the run silently produced nothing.
            for attempt in (1, 2, 3):
                try:
                    r = client.messages.parse(
                        model=FACT_MODEL, max_tokens=800, system=FACT_SYS,
                        messages=[{"role": "user", "content":
                                   f"{s['sci']} ({s['name']})"}],
                        output_format=SpeciesFact)
                    SPEND.add(r.usage)
                    return tid, r.parsed_output
                except Exception as e:                      # noqa: BLE001
                    if attempt == 3:
                        log(f"    fact failed {s['name']}: {type(e).__name__}"
                            f" (after {attempt} tries)")
                        return tid, None
                    time.sleep(0.5 * attempt)
            return tid, None

        done = 0
        by_tid = {str(s["taxon_id"]): s for s in todo}
        with ThreadPoolExecutor(max_workers=TAG_WORKERS) as pool:
            for fut in as_completed([pool.submit(ask, s) for s in todo]):
                tid, out = fut.result()
                done += 1
                if out is None:
                    cache[tid] = {"status": "error"}
                elif out.has_fact and out.fact:
                    cache[tid] = {"status": "found", "fact": out.fact.strip(),
                                  "category": out.category, "checked": None}
                else:
                    cache[tid] = {"status": "none"}
                if done % 50 == 0:                    # paid work, checkpoint it
                    cache_write("facts.json", cache)
                    log(f"      {done}/{len(todo)}")
        cache_write("facts.json", cache)

    found = [t for t, v in cache.items() if v.get("status") == "found"]
    log(f"5f/6 facts       {len(found)} of {len(cache)} species have one "
        f"({len(found)/max(1,len(cache))*100:.0f}%)")

    if not refute:
        return cache
    rank = {str(s["taxon_id"]): s for s in species}
    pool_ = sorted((t for t in found if rank.get(t)),
                   key=lambda t: -rank[t]["annual"])[:FACT_REFUTE_TOP]
    pool_ = [t for t in pool_ if cache[t].get("checked") is None]
    if not pool_:
        log("5g/6 fact check  all checked")
        return cache
    _require_key("5g/6 fact check")
    log(f"5g/6 fact check  {len(pool_)} most-seen on {FACT_REFUTE_MODEL} "
        f"(~${len(pool_)*0.037:.2f})")
    client = anthropic.Anthropic()

    def check(tid):
        s = rank[tid]
        # The structured output comes back malformed perhaps one time in ten —
        # once with "true_of_this_species=false" appended into the reason
        # string. It is not the claim that is unparseable, it is that attempt:
        # every one of these succeeded when simply asked again. Retrying is the
        # difference between checking a fact and silently dropping it.
        for attempt in (1, 2, 3):
            try:
                r = client.messages.parse(
                    model=FACT_REFUTE_MODEL, max_tokens=1200,
                    system=FACT_REFUTE_SYS,
                    messages=[{"role": "user", "content":
                               f"{s['sci']} ({s['name']}). "
                               f"Claim: {cache[tid]['fact']}"}],
                    output_format=FactVerdict)
                SPEND.add(r.usage)
                return tid, r.parsed_output
            except Exception as e:                          # noqa: BLE001
                if attempt == 3:
                    log(f"    check failed {s['name']}: {type(e).__name__}"
                        f" (after {attempt} tries)")
                    return tid, None
                time.sleep(0.5 * attempt)
        return tid, None

    killed, kept, done, errors = [], 0, 0, 0
    with ThreadPoolExecutor(max_workers=TAG_WORKERS) as pool2:
        for fut in as_completed([pool2.submit(check, t) for t in pool_]):
            tid, v = fut.result()
            done += 1
            if v is None:
                # Unverified is not the same as verified. Leave checked unset so
                # a later run retries it, and the app can tell the difference.
                cache[tid]["checked"] = None
                errors += 1
                continue
            if v.true_of_this_species:
                cache[tid]["checked"] = True
                kept += 1
            else:
                killed.append((rank[tid]["annual"], rank[tid]["name"],
                               cache[tid]["fact"], v.reason or ""))
                # Keep the category on the corpse. Replacing the whole record
                # meant a refuted fact lost the one field that says what KIND
                # of claim it was, so "which categories does the model get
                # wrong" became unanswerable over 94 refutations.
                cache[tid] = {"status": "refuted", "was": cache[tid]["fact"],
                              "category": cache[tid].get("category"),
                              "why": v.reason}
            if done % 40 == 0:
                cache_write("facts.json", cache)
                # Say something. The refute pass ran silently for minutes with
                # no way to tell progress from a stall except by stat-ing the
                # cache file.
                log(f"      {done}/{len(pool_)}  {kept} held, {len(killed)} refuted")
    cache_write("facts.json", cache)
    killed.sort(reverse=True)
    log(f"5g/6 fact check  {kept} held, {len(killed)} refuted and dropped"
        + (f", {errors} could not be checked" if errors else ""))
    for a, n, f, why in killed[:10]:
        log(f"      {a:4d}  {n[:24]:24s} {f[:60]}")
        if why:
            log(f"            -> {why[:80]}")
    print(SPEND.report())
    return cache

def apply_fact_overrides(species: list, facts: dict) -> dict:
    """Hand rulings on individual claims, read after the model and the refute
    pass and before emit.

    The refute pass catches what is *checkably* wrong. It does not catch a
    claim that is repeated in every popular source and still not true — the
    Port Jackson egg case is the case in point: "she screws it into a crevice"
    is everywhere, survived refutation, and is not what she does. A person who
    knows the animal has to be able to overrule the model, and that ruling has
    to survive every future re-bake.

    Keyed by scientific name. A value of null suppresses the fact entirely; a
    string replaces it and is marked as hand-written rather than generated.
    """
    path = ROOT / "tools" / "fact-overrides.json"
    if not path.exists():
        return facts
    ov = {k: v for k, v in json.loads(path.read_text("utf-8")).items()
          if not k.startswith("_")}
    by_sci = {s["sci"]: str(s["taxon_id"]) for s in species}
    killed = replaced = unknown = 0
    for sci, rule in ov.items():
        tid = by_sci.get(sci)
        if tid is None:
            unknown += 1
            continue
        text = rule.get("fact") if isinstance(rule, dict) else rule
        rec = dict(facts.get(tid) or {})
        if text:
            rec.update({"status": "found", "fact": text, "checked": True,
                        "by_hand": True,
                        "why": (rule or {}).get("why") if isinstance(rule, dict) else None})
            replaced += 1
        else:
            rec = {"status": "suppressed",
                   "was": rec.get("fact"),
                   "why": (rule or {}).get("why") if isinstance(rule, dict) else None}
            killed += 1
        facts[tid] = rec
    if killed or replaced or unknown:
        log(f"5h/6 fact rulings  {killed} suppressed, {replaced} replaced by hand"
            + (f", {unknown} name not in the list" if unknown else ""))
    return facts

# ONE RECORD PER SPECIES IS A FINDABILITY BUG.
#
# The card holds a single colour, and for a great many reef species that colour
# is the adult male. A swimmer who saw the brown-green female Eastern Blue
# Groper and taps "brown" gets nothing back, because the record says blue. The
# verified facts are full of this — Crimsonband Wrasse, Southern Maori Wrasse,
# Sunset Wrasse, White-ear, Orangeblotch Surgeonfish, Three-spot Dascyllus all
# came back as some version of "the two things you think you saw are one
# animal" — but prose is the one place a filter cannot reach.
#
# So the appearances are data. A facet matches if ANY morph matches, and the
# identikit draws the morph that matched: showing a blue fish to someone who
# saw a brown one is the same bug in visual form.
#
# Most species have nothing to say here. Barnacles, corals, sponges, sea stars
# and the great majority of fish look the same whatever their sex, and the
# model is told to say so.

MORPH_MODEL = MODEL
MorphLabel = Literal["male", "female", "juvenile", "adult",
                     "initial-phase", "terminal-phase"]


class Morph(BaseModel):
    label: MorphLabel
    colour: Optional[Colour] = None
    colour2: Optional[Colour] = None
    markings: List[Marking] = []
    describe: Optional[str] = None      # short phrase for the card


class MorphSet(BaseModel):
    differs: bool
    morphs: List[Morph] = []


MORPH_SYS = (
    "You are describing whether a marine species looks DIFFERENT enough between "
    "sexes, or between young and adult, that an ocean swimmer would take them "
    "for two different animals.\n\n"
    "Set differs=false for the great majority. Most species look the same "
    "whatever their sex, and invertebrates almost always do. A subtle "
    "difference in size or fin length is NOT enough — the test is whether "
    "someone in a mask would think they had seen two species.\n\n"
    "Set differs=true for the cases where the difference is a whole change of "
    "appearance: wrasses and parrotfishes whose initial and terminal phases "
    "differ completely, damselfishes whose juveniles are a different colour "
    "entirely, gropers where one sex is blue and the other brown.\n\n"
    "When it is true, give one entry per appearance a swimmer would meet, using "
    "ONLY the supplied colour and marking vocabularies, and a describe phrase "
    "under 70 characters written for someone looking at the animal. Use "
    "initial-phase and terminal-phase for wrasses where the change follows sex "
    "reversal rather than simple sex, and juvenile only where the young differ "
    "from BOTH adult forms.")

MORPH_REFUTE_SYS = (
    "You are checking a claim that a marine species has two or more distinct "
    "appearances. Assume it is wrong and try to show that.\n\n"
    "Set true_of_this_species=false if the species does not actually differ "
    "this way, if the difference is far too subtle for a swimmer to notice, if "
    "the colours named are wrong, or if the pattern described belongs to a "
    "different species. Uncertainty is a refutation.\n\n"
    "Keep reason under 300 characters. One or two sentences, no preamble.")


def stage_morphs(species: list, force: bool, refute: bool = True) -> dict:
    cache = {} if force else (cache_read("morphs.json") or {})
    todo = [s for s in species
            if (cache.get(str(s["taxon_id"])) or {}).get("status", "missing")
            in ("missing", "error")]
    if todo:
        _require_key("5i/6 morphs")
        log(f"5i/6 morphs      {len(todo)} on {MORPH_MODEL} (~${len(todo)*0.02:.2f})")
        client = anthropic.Anthropic()

        def ask(s):
            tid = str(s["taxon_id"])
            for attempt in (1, 2, 3):
                try:
                    r = client.messages.parse(
                        model=MORPH_MODEL, max_tokens=1200, system=MORPH_SYS,
                        messages=[{"role": "user", "content":
                                   f"{s['sci']} ({s['name']})"}],
                        output_format=MorphSet)
                    SPEND.add(r.usage)
                    return tid, r.parsed_output
                except Exception as e:                      # noqa: BLE001
                    if attempt == 3:
                        log(f"    morph failed {s['name']}: {type(e).__name__}")
                        return tid, None
                    time.sleep(0.5 * attempt)
            return tid, None

        done = 0
        with ThreadPoolExecutor(max_workers=TAG_WORKERS) as pool:
            for fut in as_completed([pool.submit(ask, s) for s in todo]):
                tid, out = fut.result()
                done += 1
                if out is None:
                    cache[tid] = {"status": "error"}
                elif out.differs and len(out.morphs) >= 2:
                    cache[tid] = {"status": "found", "checked": None,
                                  "morphs": [m.model_dump() for m in out.morphs]}
                else:
                    # differs=true with one morph says nothing; treat as none.
                    cache[tid] = {"status": "none"}
                if done % 50 == 0:
                    cache_write("morphs.json", cache)
                    log(f"      {done}/{len(todo)}")
        cache_write("morphs.json", cache)

    found = [t for t, v in cache.items() if v.get("status") == "found"]
    log(f"5i/6 morphs      {len(found)} of {len(cache)} species have more than "
        f"one appearance ({len(found)/max(1,len(cache))*100:.0f}%)")
    if not refute:
        return cache

    rank = {str(s["taxon_id"]): s for s in species}
    pool_ = [t for t in found if rank.get(t) and cache[t].get("checked") is None]
    if not pool_:
        log("5j/6 morph check all checked")
        return cache
    _require_key("5j/6 morph check")
    log(f"5j/6 morph check {len(pool_)} on {MORPH_MODEL} (~${len(pool_)*0.015:.2f})")
    client = anthropic.Anthropic()

    def describe(tid):
        return "; ".join(
            f"{m['label']}: {m.get('describe') or ''} "
            f"({m.get('colour') or '?'}{'/'+m['colour2'] if m.get('colour2') else ''})"
            for m in cache[tid]["morphs"])

    def check(tid):
        s = rank[tid]
        for attempt in (1, 2, 3):
            try:
                r = client.messages.parse(
                    model=MORPH_MODEL, max_tokens=1200, system=MORPH_REFUTE_SYS,
                    messages=[{"role": "user", "content":
                               f"{s['sci']} ({s['name']}). "
                               f"Claim: {describe(tid)}"}],
                    output_format=FactVerdict)
                SPEND.add(r.usage)
                return tid, r.parsed_output
            except Exception as e:                          # noqa: BLE001
                if attempt == 3:
                    log(f"    morph check failed {s['name']}: {type(e).__name__}")
                    return tid, None
                time.sleep(0.5 * attempt)
        return tid, None

    kept, killed, errors, done = 0, [], 0, 0
    with ThreadPoolExecutor(max_workers=TAG_WORKERS) as pool2:
        for fut in as_completed([pool2.submit(check, t) for t in pool_]):
            tid, v = fut.result()
            done += 1
            if v is None:
                cache[tid]["checked"] = None
                errors += 1
            elif v.true_of_this_species:
                cache[tid]["checked"] = True
                kept += 1
            else:
                killed.append((rank[tid]["annual"], rank[tid]["name"], v.reason or ""))
                cache[tid] = {"status": "refuted", "why": v.reason,
                              "was": cache[tid].get("morphs")}
            if done % 40 == 0:
                cache_write("morphs.json", cache)
                log(f"      {done}/{len(pool_)}  {kept} held, {len(killed)} refuted")
    cache_write("morphs.json", cache)
    killed.sort(reverse=True)
    log(f"5j/6 morph check {kept} held, {len(killed)} refuted"
        + (f", {errors} could not be checked" if errors else ""))
    for a, n, why in killed[:8]:
        log(f"      {a:4d}  {n[:26]:26s} {why[:60]}")
    print(SPEND.report())
    return cache

# TWO PLACES SAYING THE SAME THING WILL EVENTUALLY SAY DIFFERENT THINGS.
#
# The fact pass and the morph pass were run independently, and for 53 species
# they describe the same animal from two directions. Today they agree — eleven
# were re-derived cold and matched every time — but they are regenerated
# separately, so a future re-bake can leave a card asserting one thing in prose
# and another in its filter.
#
# The morph block is the one that feeds the filter, so it is the copy that
# stays. But a blanket drop would be wrong: Senator Wrasse's fact is about a
# Roman toga, Blind Shark's about clamping its eyes shut, Three-spot Dascyllus
# about sheltering in anemone tentacles. Those sit alongside morphs without
# duplicating them. Only the restatements go, and only a reading of both can
# tell which is which.

RETIRE_SYS = (
    "You are given structured data listing a marine species' distinct "
    "appearances, and a one-line prose fact about the same species.\n\n"
    "Answer says_the_same_thing=true ONLY if the prose is essentially a "
    "restatement of the appearance data — if a reader who had already seen the "
    "appearance list would learn nothing new from it.\n\n"
    "Answer false if the prose adds anything the list does not carry: where a "
    "name comes from, a behaviour, a habitat, a defence, a lifespan, what "
    "triggers the change, or any detail beyond which forms exist and what "
    "colour they are. Mentioning the same forms is not enough to make it a "
    "restatement — it must add nothing.\n\n"
    "Keep reason under 200 characters."
)


class Redundant(BaseModel):
    says_the_same_thing: bool
    reason: Optional[str] = None


def stage_retire_facts(species: list, facts: dict, morphs: dict) -> dict:
    """Drop facts that only restate the morph data, keep the ones that add."""
    rank = {str(s["taxon_id"]): s for s in species}
    todo = [t for t, v in facts.items()
            if v.get("status") == "found" and v.get("checked") is True
            and not v.get("by_hand")
            and (morphs.get(t) or {}).get("checked") is True
            and t in rank]
    if not todo:
        log("5k/6 retire      nothing overlaps")
        return facts
    _require_key("5k/6 retire")
    log(f"5k/6 retire      {len(todo)} facts sit beside morph data (~${len(todo)*0.012:.2f})")
    client = anthropic.Anthropic()

    def shape_of(tid):
        return "; ".join(
            f"{m['label']}: {m.get('describe') or ''} "
            f"({m.get('colour') or '?'}{'/' + m['colour2'] if m.get('colour2') else ''})"
            for m in morphs[tid]["morphs"])

    def judge(tid):
        s = rank[tid]
        for attempt in (1, 2, 3):
            try:
                r = client.messages.parse(
                    model=MODEL, max_tokens=1000, system=RETIRE_SYS,
                    messages=[{"role": "user", "content":
                               f"{s['sci']} ({s['name']})\n"
                               f"Appearances: {shape_of(tid)}\n"
                               f"Prose fact: {facts[tid]['fact']}"}],
                    output_format=Redundant)
                SPEND.add(r.usage)
                return tid, r.parsed_output
            except Exception as e:                          # noqa: BLE001
                if attempt == 3:
                    log(f"    retire check failed {s['name']}: {type(e).__name__}")
                    return tid, None
                time.sleep(0.5 * attempt)
        return tid, None

    dropped, kept, done = [], [], 0
    with ThreadPoolExecutor(max_workers=TAG_WORKERS) as pool:
        for fut in as_completed([pool.submit(judge, t) for t in todo]):
            tid, v = fut.result()
            done += 1
            if v is None:
                continue                       # unjudged: leave the fact alone
            if v.says_the_same_thing:
                dropped.append((rank[tid]["annual"], rank[tid]["name"],
                                facts[tid]["fact"]))
                facts[tid] = {"status": "retired-overlaps-morphs",
                              "was": facts[tid]["fact"], "why": v.reason}
            else:
                kept.append((rank[tid]["annual"], rank[tid]["name"]))
            if done % 25 == 0:
                cache_write("facts.json", facts)
    cache_write("facts.json", facts)
    dropped.sort(reverse=True); kept.sort(reverse=True)
    log(f"5k/6 retire      {len(dropped)} retired, {len(kept)} kept as additive")
    for a, n, f in dropped[:8]:
        log(f"      drop {a:4d}  {n[:26]:26s} {f[:58]}")
    for a, n in kept[:6]:
        log(f"      keep {a:4d}  {n}")
    print(SPEND.report())
    return facts

def apply_morph_overrides(species: list, morphs: dict) -> dict:
    """Hand rulings on morph sets, read immediately before emit.

    A morph is the one attribute where a wrong value files a species under a
    colour it never wears — the failure the whole feature exists to prevent,
    pointing the other way. The refute pass caught 9 of 102, but it is a second
    model checking a first, and agreement between two passes is evidence rather
    than proof. This is where a person overrules both, and the ruling survives
    every regeneration.

    Keyed by scientific name. null drops the set; an object with a morphs list
    replaces it. Produced by tools/morph-review.html.
    """
    path = ROOT / "tools" / "morph-overrides.json"
    if not path.exists():
        return morphs
    ov = {k: v for k, v in json.loads(path.read_text("utf-8")).items()
          if not k.startswith("_")}
    by_sci = {s["sci"]: str(s["taxon_id"]) for s in species}
    dropped = replaced = unknown = 0
    for sci, rule in ov.items():
        tid = by_sci.get(sci)
        if tid is None:
            unknown += 1
            continue
        if rule is None or (isinstance(rule, dict) and not rule.get("morphs")):
            morphs[tid] = {"status": "ruled-out-by-hand",
                           "was": (morphs.get(tid) or {}).get("morphs")}
            dropped += 1
        else:
            morphs[tid] = {"status": "found", "checked": True, "by_hand": True,
                           "morphs": rule["morphs"]}
            replaced += 1
    if dropped or replaced or unknown:
        log(f"5m/6 morph rulings {dropped} dropped, {replaced} corrected by hand"
            + (f", {unknown} name not in the list" if unknown else ""))
    return morphs

def season_bay_agreement(species: list, months: dict, season: dict) -> dict:
    """How well each species' NSW season matches what the BAY actually recorded.

    The season is derived entirely from NSW, so the bay's own monthly counts are
    an independent check on whether it transfers. Measured over the 49 seasonal
    species with enough bay records: median r = +0.76, 47 of 49 positive, against
    a chance baseline of +0.25 (chance is not zero because bay and NSW observing
    share a calendar). So it does transfer — mostly.

    Mostly is the point. Rough Leatherjacket has 215 bay records and r = +0.03:
    NSW says September and the bay says nothing of the kind. Estuary Cobbler is
    -0.17, actively backwards. Saying "more often seen in September" about those
    is a small error. Saying "you won't see it" is the swimmer standing in the
    water looking at one, which is the failure the whole app is written to avoid.

    So the number is emitted and the UI spends it asymmetrically: the positive
    claim may lean on NSW alone, the negative one may not.
    """
    # stage_months returns {"by_taxon": {...}, "effort": [...]}, not a bare map
    # of taxon -> counts. Reaching straight for .values() got the two top-level
    # keys instead of 640 species.
    by_taxon = months.get("by_taxon", months)
    tot = [0] * 12
    for m in by_taxon.values():
        if not isinstance(m, list):
            continue
        for i, c in enumerate(m[:12]):
            tot[i] += c

    def corr(a, b):
        ma, mb = sum(a) / 12, sum(b) / 12
        na = sum((x - ma) ** 2 for x in a)
        nb = sum((y - mb) ** 2 for y in b)
        if na <= 0 or nb <= 0:
            return None
        return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / ((na * nb) ** 0.5)

    out, tested = {}, 0
    for s in species:
        tid = str(s["taxon_id"])
        sea = season.get(tid) or {}
        m = by_taxon.get(str(s["taxon_id"])) or by_taxon.get(s["taxon_id"])
        if not isinstance(m, list):
            continue
        if sea.get("state") != "real" or not sea.get("share") or not m:
            continue
        # BAY_MIN_FOR_AGREEMENT: below this the correlation is noise, and a
        # noisy +0.9 is more dangerous than an honest null.
        if sum(m) < BAY_MIN_FOR_AGREEMENT:
            continue
        bay = [m[i] / tot[i] if tot[i] else 0 for i in range(12)]
        t = sum(bay) or 1
        r = corr([x / t for x in bay], sea["share"])
        if r is None:
            continue
        out[tid] = round(r, 3)
        tested += 1
    if tested:
        vals = sorted(out.values())
        log(f"2c/6 season check {tested} seasonal species testable against bay "
            f"records; median r {vals[len(vals)//2]:+.2f}, "
            f"{sum(1 for v in vals if v >= SEASON_BAY_MIN)} confirmed locally")
    return out

# CHOOSING THE PHOTOGRAPH, INSTEAD OF INHERITING A POPULARITY CONTEST.
#
# iNaturalist ranks by community votes, which reward a good photograph. An
# identification guide needs a legible one, and those are different things.
# Auditing the first hundred species by eye produced twenty replacements and
# four repeatable failure modes:
#
#   the face over the profile   6 of 8 top Blue Groper shots are head-on
#   the curiosity over the animal   a bleached snapper SKULL ranked first;
#                                   squid EGGS ranked fourth
#   the dead over the living    Sixspine Leatherjacket washed up on rock
#   the catch over the fish     half the top Bluefish shots are in a hand
#
# Two of the hundred were not even the right animal, both ranked first: the
# snapper skull, and a MUSSEL filed as a Smooth Toadfish.
#
# The criterion is statable, so it does not need eyes: one whole living animal,
# side on, in the water. This shows the model every candidate at once and asks
# it to choose, which is the same judgement made 540 more times.

class PhotoChoice(BaseModel):
    best: int                    # index into the candidates shown
    whole_animal: bool
    in_water: bool
    wrong_animal: bool
    why: Optional[str] = None


BEST_PHOTO_SYS = (
    "You are choosing which photograph best identifies a marine species for a "
    "swimmer who has just seen it and wants to name it.\n\n"
    "Choose the one showing ONE WHOLE LIVING ANIMAL, SIDE ON, IN THE WATER.\n\n"
    "Strongly prefer, in order:\n"
    "1. the whole animal in frame, not a head or a crop of its middle\n"
    "2. side on rather than face on — a portrait fills a frame and identifies "
    "nothing\n"
    "3. alive and underwater — not held in a hand, not in a bag or a boat, not "
    "washed up, not a shell or a skull or an egg mass\n"
    "4. one animal, not a school or a pair that could be two species\n"
    "5. sharp and well lit, the body pattern legible\n\n"
    "A beautiful photograph that fails these is the wrong choice. Return the "
    "index of the best candidate even when all are poor, and set the flags to "
    "describe the one you chose. Set wrong_animal only if the chosen picture "
    "plainly shows a different species from the one named.")


def stage_best_photo(species: list, photos: dict, force: bool) -> dict:
    """Pick the most legible candidate per species, not the best-voted one."""
    picks = {} if force else (cache_read("photo-picks.json") or {})
    todo = [s for s in species
            if str(s["taxon_id"]) not in picks
            and len(photos.get(str(s["taxon_id"])) or []) > 1]
    if not todo:
        log(f"3b/6 best photo  {len(picks):>4} (cached)")
        return picks
    _require_key("3b/6 best photo")
    log(f"3b/6 best photo  {len(todo)} species with a choice to make "
        f"(~${len(todo)*0.021:.2f})")
    client = anthropic.Anthropic()

    def choose(s):
        tid = str(s["taxon_id"])
        cands = (photos.get(tid) or [])[:3]
        blocks, kept = [], []
        for c in cands:
            f = OUT_DIR / c["file"]
            if not f.exists() and not download_square(c["url"], f):
                continue
            try:
                b64 = base64.standard_b64encode(f.read_bytes()).decode()
            except Exception:                                # noqa: BLE001
                continue
            blocks.append({"type": "text", "text": f"Candidate {len(kept)}:"})
            blocks.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/webp", "data": b64}})
            kept.append(c)
        if len(kept) < 2:
            return tid, None, kept
        blocks.append({"type": "text",
                       "text": f"Which candidate best identifies {s['name']} "
                               f"({s['sci']})?"})
        for attempt in (1, 2, 3):
            try:
                r = client.messages.parse(
                    model=MODEL, max_tokens=500, system=BEST_PHOTO_SYS,
                    messages=[{"role": "user", "content": blocks}],
                    output_format=PhotoChoice)
                SPEND.add(r.usage)
                return tid, r.parsed_output, kept
            except Exception as e:                           # noqa: BLE001
                if attempt == 3:
                    log(f"    failed on {s['name']}: {type(e).__name__}")
                    return tid, None, kept
                time.sleep(1.5 * attempt)
        return tid, None, kept

    moved, done = [], 0
    rank = {str(s["taxon_id"]): s for s in todo}
    with ThreadPoolExecutor(max_workers=TAG_WORKERS) as pool:
        for fut in as_completed([pool.submit(choose, s) for s in todo]):
            tid, out, kept = fut.result()
            done += 1
            if out is not None and 0 <= out.best < len(kept):
                picks[tid] = {"photo_id": kept[out.best]["photo_id"],
                              "was": kept[0]["photo_id"],
                              "whole": out.whole_animal, "in_water": out.in_water,
                              "wrong": out.wrong_animal, "why": out.why}
                if out.best != 0:
                    moved.append((rank[tid]["annual"], rank[tid]["name"], out.why))
            if done % 40 == 0:
                cache_write("photo-picks.json", picks, allow_shrink=True)
                log(f"      {done}/{len(todo)}  {len(moved)} moved off the top vote")
    cache_write("photo-picks.json", picks, allow_shrink=True)
    moved.sort(reverse=True)
    poor = [t for t, v in picks.items()
            if not v.get("whole") or not v.get("in_water") or v.get("wrong")]
    log(f"3b/6 best photo  {len(moved)} of {len(todo)} moved off the best-voted shot; "
        f"{len(poor)} still imperfect after choosing")
    for a, n, why in moved[:10]:
        log(f"      {a:4d}  {n[:26]:26s} {(why or '')[:56]}")
    print(SPEND.report())
    return picks

def bay_provenance(species: list) -> dict:
    """How many observations the app is built on, by how many people, since when.

    Hardcoded in the page as "ten years of observations by 206 contributors",
    and both halves had gone stale: 302 people have contributed now, and the
    record reaches back to 1998 rather than ten years. A number typed into a
    sentence has no way of aging with the data behind it, which is the whole
    reason it drifted.
    """
    total = sum(s.get("annual") or 0 for s in species)
    out = {"observations": total, "species": len(species)}
    try:
        d = inat("observations/observers", place_id=PLACE_ID, taxon_id=TAXA,
                 verifiable="true", per_page=1)
        out["contributors"] = d.get("total_results")
        f = inat("observations", place_id=PLACE_ID, taxon_id=TAXA,
                 verifiable="true", order_by="observed_on", order="asc", per_page=1)
        r = (f.get("results") or [{}])[0].get("observed_on")
        if r:
            out["since"] = str(r)[:4]
    except Exception as e:                                   # noqa: BLE001
        log(f"    provenance lookup failed: {type(e).__name__}")
    log(f"1b/6 provenance  {out.get('observations'):,} observations by "
        f"{out.get('contributors')} people since {out.get('since')}")
    return out

def summer_visitors(species: list, season: dict) -> dict:
    """The species that arrive on the current and do not last the winter.

    The East Australian Current carries tropical larvae south each summer.
    They settle here, are seen through autumn, and are gone by spring — they do
    not survive a Sydney winter. The record shows it plainly and at a scale
    worth putting on the page: their combined sightings run 601 in April to 7
    in November, and they are an eighth of everything ever recorded in the bay.

    Defined as a real season peaking February to June that has all but emptied
    by September and October. That is deliberately the shape of the pattern
    rather than a list of families, so a resident species with the same timing
    would qualify and a tropical one that overwinters would not.
    """
    sea = (season or {}).get("species") or {}
    out, months = [], [0] * 12
    for s in species:
        v = sea.get(str(s["taxon_id"])) or {}
        sh = v.get("share")
        if v.get("state") != "real" or not sh:
            continue
        if v.get("peak") not in (1, 2, 3, 4, 5):
            continue
        if sh[8] > (1 / 12) * 0.65 or sh[9] > (1 / 12) * 0.65:
            continue
        out.append(s["taxon_id"])
        for i, c in enumerate((s.get("months") or [])[:12]):
            months[i] += c
    if not out:
        return {}
    peak, trough = months.index(max(months)), months.index(min(months))
    log(f"2d/6 visitors    {len(out)} species arrive on the current; "
        f"sightings peak {max(months)} and fall to {min(months)}")
    return {"taxa": out, "months": months, "peak": peak, "trough": trough}

# Sea-surface temperature at the bay, monthly mean over 2016-2025, from the
# Open-Meteo marine archive.
#
# READ THIS BEFORE REUSING IT. The swim app's sea-temperature tile has exactly
# ONE source — the community reading scraped from a Facebook post — and
# Open-Meteo is deliberately never used for it. That rule is about reporting
# what the water is doing TODAY, where a model must not stand in for someone
# who actually got in. These twelve numbers are a ten-year average used to
# explain why the summer visitors turn up when they do. They are never shown as
# a reading, never presented as current, and never reach the swim app. Baked as
# constants rather than fetched so this cannot quietly become a live feed.
BAY_SST_CLIMATOLOGY = [22.7, 23.6, 23.2, 22.0, 20.9, 19.4,
                       18.0, 18.0, 18.7, 19.4, 20.4, 21.2]


def bay_year(species: list, visitors: dict) -> dict:
    """The bay's own calendar: when it is busiest, most varied, and rarest.

    April is the peak of everything, not only of the visitors — 2,570 sightings
    and 348 distinct species against July's 816. And the record has a long tail
    almost nobody sees: 173 of the 640 species have been recorded here exactly
    once in twenty-eight years.
    """
    months = [0] * 12
    distinct = [0] * 12
    for s in species:
        m = (s.get("months") or [0] * 12)[:12]
        for i, c in enumerate(m):
            months[i] += c
            if c:
                distinct[i] += 1
    once = sum(1 for s in species if (s.get("annual") or 0) == 1)
    twice = sum(1 for s in species if (s.get("annual") or 0) <= 2)
    out = {"months": months, "distinct": distinct,
           "busiest": months.index(max(months)),
           "quietest": months.index(min(months)),
           "most_varied": distinct.index(max(distinct)),
           "seen_once": once, "seen_twice_or_less": twice,
           "sst": BAY_SST_CLIMATOLOGY}

    # How closely the visitors follow the water, and at what delay. Measured
    # rather than asserted: same-month agreement is only +0.44, two months
    # earlier is +0.93. The fish are answering the temperature that was there
    # when they were drifting, not the one they arrive into.
    vm = (visitors or {}).get("months")
    if vm:
        def corr(a, b):
            ma, mb = sum(a) / 12, sum(b) / 12
            na = sum((x - ma) ** 2 for x in a)
            nb = sum((y - mb) ** 2 for y in b)
            return (sum((x - ma) * (y - mb) for x, y in zip(a, b))
                    / ((na * nb) ** 0.5)) if na and nb else 0
        best, br = 0, -2
        for lag in range(4):
            sh = BAY_SST_CLIMATOLOGY[-lag:] + BAY_SST_CLIMATOLOGY[:-lag] if lag else BAY_SST_CLIMATOLOGY
            r = corr(vm, sh)
            if r > br:
                best, br = lag, r
        out["sst_lag"] = best
        out["sst_r"] = round(br, 2)
        log(f"2e/6 bay year    busiest {out['busiest']+1}, {once} species seen once; "
            f"visitors track SST {best} months earlier (r {br:+.2f})")
    return out

def _get_json(url: str):
    """Plain JSON GET for services that are not iNaturalist."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return json.loads(urllib.request.urlopen(req, timeout=40).read())


def _gbif_accept(sci: str, d: dict):
    """Take a GBIF match only when it is certainly the same animal.

    EXACT is obviously fine. Beyond that there are two cases worth taking and a
    great many worth refusing.

    An EXACT match sometimes hides in the alternatives while the headline
    answer is a family — Chrysophrys auratus came back as "Sparidae" with the
    real species sitting underneath it.

    And Latin adjectives agree in gender with the genus, so the same fish is
    spelled tenuicaudata or tenuicaudatus, cubicum or cubicus, annularis or
    annulatus, depending on which genus it currently sits in. Those are not
    different species and refusing them costs the Southern Eagle Ray its map.
    Accepted only when the genus is identical and the epithets differ solely in
    their ending, which is what gender agreement can do and what a different
    species cannot.
    """
    def species_key(rec):
        return (rec.get("usageKey") if rec.get("rank") == "SPECIES"
                and rec.get("usageKey") else None)

    if d.get("matchType") == "EXACT" and species_key(d):
        return species_key(d)
    for alt in (d.get("alternatives") or []):
        if alt.get("matchType") == "EXACT" and species_key(alt):
            return species_key(alt)

    want = sci.split()
    for rec in [d] + list(d.get("alternatives") or []):
        got = (rec.get("scientificName") or "").split()
        if (rec.get("matchType") == "FUZZY" and species_key(rec)
                and (rec.get("confidence") or 0) >= 90
                and len(want) >= 2 and len(got) >= 2
                and want[0] == got[0]):
            a, b = want[1], got[1]
            stem = min(len(a), len(b)) - 3          # allow only the ending to move
            if stem >= 4 and a[:stem] == b[:stem] and abs(len(a) - len(b)) <= 2:
                return species_key(rec)
    return None

def stage_gbif_keys(species: list, force: bool) -> dict:
    """Match each species to GBIF, so the card can show where it lives.

    The page can say what an animal looks like and when it turns up, and had no
    way to say whether it belongs here. That distinction is the whole summer
    visitor story seen from the other end: the Eastern Blue Groper sits in one
    tight cluster on this coast and nowhere else on earth, while the Moorish
    Idol runs in a band across the entire Indo-Pacific and only calls in here.

    Only the key is stored. The map itself is two tiles fetched by the browser
    when a card opens, so nothing is downloaded for the 639 cards nobody looks
    at, and no map data is baked into a file that would go stale.
    """
    keys = {} if force else (cache_read("gbif.json") or {})
    todo = [s for s in species if str(s["taxon_id"]) not in keys]
    if not todo:
        log(f"3c/6 gbif        {len(keys):>4} (cached)")
        return keys
    log(f"3c/6 gbif        {len(todo)} to match (free)")
    ok = 0
    for i, s in enumerate(todo, 1):
        tid = str(s["taxon_id"])
        try:
            d = _get_json("https://api.gbif.org/v1/species/match?verbose=true&name="
                          + urllib.parse.quote(s["sci"]))
            keys[tid] = _gbif_accept(s["sci"], d)
            ok += 1 if keys[tid] else 0
        except Exception:                                    # noqa: BLE001
            pass
        if i % 100 == 0:
            cache_write("gbif.json", keys, allow_shrink=True)
            log(f"      {i}/{len(todo)}")
    cache_write("gbif.json", keys, allow_shrink=True)
    matched = sum(1 for v in keys.values() if v)
    log(f"3c/6 gbif        {matched} of {len(keys)} matched exactly")
    return keys

def stage_shape_by_name(species: list, tags: dict) -> dict:
    _require_key("5e/6 shape by name")
    client = anthropic.Anthropic()
    # EVERY species, not just the ones whose photo failed. Shape is a property
    # of the animal — a wrasse is torpedo-shaped whatever angle it was shot
    # from — so reading it off a single photograph was the wrong instrument all
    # along. It is also why shape needed a ballot: two reads of the same
    # picture disagreed 24% of the time, which is variance in the photograph,
    # not in the fish.
    #
    # An unclear photograph is now a separate problem with a separate fix:
    # replace the photograph. See tools/photo-overrides.json.
    # Species with no re-usable photograph were never tagged, so requiring a
    # tags entry here excluded exactly the 37 the app was already hiding. They
    # have no picture; they still have a body plan, and a shape asked by name
    # needs no photograph at all. Give them a tags record carrying shape alone.
    # Kept in its OWN file, not inside the tag record.
    #
    # A tag describes a photograph. A shape asked from the species name does
    # not, and storing it there made it collateral in every photo operation:
    # it was silently reverted three separate times in one day — by a re-tag,
    # by a hand-picked photograph deleting the record, and by a stage that
    # dropped records it wrongly believed stale. Each was patched where it
    # happened. None of that is needed once the value lives somewhere a photo
    # operation cannot reach.
    shapes = cache_read("name-shapes.json") or {}
    todo = [s for s in species if str(s["taxon_id"]) not in shapes]
    if not todo:
        log("5e/6 shape by name  all cached")
        return tags
    log(f"5e/6 shape by name  {len(todo)} species (~${len(todo)*0.0088:.2f})")

    def ask(s):
        tid = str(s["taxon_id"])
        try:
            resp = client.messages.parse(
                model=MODEL, max_tokens=500, system=SHAPE_BY_NAME_SYS,
                messages=[{"role": "user", "content":
                           f"{s['sci']} ({s['name']}) — which body silhouette?"}],
                output_format=ShapeVote)
            SPEND.add(resp.usage)
            return tid, resp.parsed_output
        except Exception as e:                             # noqa: BLE001
            log(f"    failed on {s['name']}: {type(e).__name__}")
            return tid, None

    # The completion loop only gets (tid, out) back — the species that produced
    # it is out of scope here. Look it up rather than reaching for the closure
    # variable: doing that raised NameError on the first differing shape and
    # threw away every answer in the run, all of them already paid for.
    by_tid = {str(s["taxon_id"]): s for s in todo}
    got, changed, done = 0, [], 0
    with ThreadPoolExecutor(max_workers=TAG_WORKERS) as pool:
        for fut in as_completed([pool.submit(ask, s) for s in todo]):
            tid, out = fut.result()
            done += 1
            if out and out.photo_usable and out.shape:   # reusing the ballot schema
                sp = by_tid[tid]
                was = (tags.get(tid) or {}).get("shape")
                shapes[tid] = {"shape": out.shape,
                               "from": "species knowledge, not the photograph"}
                if was and was != out.shape:
                    shapes[tid]["was_photo"] = was
                    changed.append((sp["annual"], sp["name"], was, out.shape))
                got += 1
            # Checkpoint. Anything already answered is money spent; a crash or a
            # Ctrl-C after this point costs re-running only the tail.
            if done % 40 == 0:
                cache_write("name-shapes.json", shapes)
                log(f"      {done}/{len(todo)}  {got} resolved")
    cache_write("name-shapes.json", shapes)
    changed.sort(reverse=True)
    log(f"5e/6 shape by name  {got} of {len(todo)} resolved; {len(changed)} differ "
        f"from the photo read")
    for a, n, was, now in changed[:12]:
        log(f"      {a:4d}  {n[:28]:28s} {was} -> {now}")
    print(SPEND.report())
    return tags


# ── Stage 6: emit ────────────────────────────────────────────────────────────

def report_disagreement(before: dict, after: dict, species: list) -> None:
    """Two independent looks at the same animal. Where they agree, the tag is
    worth far more than one pass calling itself confident; where they differ,
    that is the queue a human should actually work through."""
    names = {str(s["taxon_id"]): s["name"] for s in species}
    counts = {str(s["taxon_id"]): s["annual"] for s in species}
    agree, changed, rescued, lost = 0, [], 0, 0
    for tid, old in before.items():
        new = after.get(tid)
        if not new:
            lost += 1
            continue
        if not (old or {}).get("photo_usable", True):
            pass
        same = (old.get("shape") == new.get("shape")
                and old.get("colour_primary") == new.get("colour_primary"))
        if same:
            agree += 1
        else:
            changed.append((counts.get(tid, 0), names.get(tid, tid), old, new))
        if new.get("confidence") == "high" and old.get("confidence") != "high":
            rescued += 1

    n = len(before)
    print()
    log(f"  SECOND OPINION over {n} previously-hedged species")
    log(f"    agree on shape + colour   {agree}  ({agree/max(1,n)*100:.0f}%)")
    log(f"    DISAGREE — review these   {len(changed)}")
    log(f"    now rated high            {rescued}")
    if lost:
        log(f"    no usable photo at all    {lost}  (every candidate rejected)")
    if changed:
        changed.sort(reverse=True)
        print()
        log("    biggest disagreements, by how often the animal is seen here:")
        for c, nm, o, nw in changed[:15]:
            print(f"      {c:4d}  {nm[:28]:28s} {o.get('shape')}/{o.get('colour_primary')}"
                  f"  ->  {nw.get('shape')}/{nw.get('colour_primary')}")


def stage_emit(species: list, months: dict, photos: dict, tags: dict,
               sizes: dict, slugs: dict, season: dict = None, facts: dict = None, morphs: dict = None, agree: dict = None, picks: dict = None, prov: dict = None, gbif: dict = None) -> None:
    now = datetime.now(timezone.utc)
    by_taxon, effort = months["by_taxon"], months["effort"]

    # Hand-curated grouping behaviour. A photograph can show that a species
    # schools; it can never show that one lives in pairs — Black Reef
    # Leatherjacket does, and a single-fish frame would have us say nothing.
    # FishBase's free text is not usable for this: its "distinct pairing"
    # phrasing refers to spawning, not company kept, and it misses 45% anyway.
    # So this stays a small hand-maintained file, and it outranks the photo.
    behaviour = {}
    bpath = ROOT / "tools" / "behaviour-overrides.json"
    if bpath.exists():
        behaviour = {k: v for k, v in json.loads(bpath.read_text("utf-8")).items()
                     if not k.startswith("_") and v}

    # Hand corrections from tools/review.html, keyed by taxon id. Applied AFTER
    # the model and BEFORE emit, so a human decision is never in the blast
    # radius of a re-bake, a vocabulary change or a re-tag.
    corrections = {}
    cpath = ROOT / "tools" / "tag-overrides.json"
    if cpath.exists():
        corrections = {k: v for k, v in json.loads(cpath.read_text("utf-8")).items()
                       if not k.startswith("_")}
        log(f"    {len(corrections)} hand corrections loaded from tag-overrides.json")

    out, review, no_photo, no_tag, corrected = [], [], 0, 0, 0
    missing_img = 0
    no_size = sum(1 for s in species if str(s["taxon_id"]) not in sizes)
    for s in species:
        tid = str(s["taxon_id"])
        t = tags.get(tid)
        cands = photos.get(tid)
        if not cands:
            no_photo += 1
        if not t:
            no_tag += 1

        # Only the candidate actually used for tagging gets downloaded, so the
        # fallback to cands[0] could name a file that was never fetched. That
        # shipped 11 species — Banjo Ray among them, at 226 records — with a
        # broken image and a photographer's credit underneath it. Fetch it if
        # it is missing; if that fails, carry neither the image nor the credit.
        # The CURRENT pick wins over whatever the tagger happened to look at.
        #
        # Reading it off the tag was right while the two could not diverge. They
        # can now: widening the photo pool from the bay to NSW replaced 504
        # pictures, and every one of them would have stayed invisible because
        # the tag still pointed at the old file.
        #
        # The cost is that a fresh pick has not been through the vision pass, so
        # nothing has checked it shows the right animal — that caught 4 wrong
        # animals in 601 last time. Re-tagging restores the check and rewrites
        # the description; until then the picture is current and the colour
        # words describe the one it replaced.
        # Prefer the photograph the VISION PASS settled on, but only while it is
        # still one of the current candidates.
        #
        # Both halves matter. The tagger walks the candidates and rejects any it
        # cannot describe, so its choice is the vetted one: for 46 species it
        # passed over the best-voted shot precisely because that shot was
        # unusable. But when the candidate list is replaced wholesale — as it
        # was moving from the bay to NSW — the tagged photo is simply gone, and
        # trusting it then would have kept 504 replaced pictures invisible.
        # Precedence: the chosen photograph, then the one the tagger vetted,
        # then the best-voted. stage_best_photo puts its choice at the head of
        # the candidate list, so preferring cands[0] when a pick exists is the
        # same as preferring the pick — but the tagged photo is still IN the
        # list, and the vetted-photo rule below would otherwise keep showing it
        # and make all 332 choices inert.
        tagged = (t or {}).get("photo")
        current = {c.get("photo_id") for c in cands}
        # The tagger has the last word, because it is the only step that has
        # LOOKED at the picture it is about to describe. Where it walked past
        # the chosen photograph to a later candidate, it did so because it could
        # not describe the chosen one — three species, and in each the tagger is
        # better evidence than the chooser. Everywhere else the two now agree,
        # so this also keeps the description and the picture in step.
        chosen = (picks or {}).get(tid, {}).get("photo_id")
        if tagged and tagged.get("photo_id") in current:
            photo = tagged
        elif chosen:
            photo = next((c for c in cands if c.get("photo_id") == chosen),
                         cands[0] if cands else tagged)
        else:
            photo = cands[0] if cands else tagged
        # A tag record stores the candidate as it looked when the tag was
        # written, so 114 of them predate the place field and the caption came
        # out with no location at all — and the hand-picked ones never had one,
        # because the override builds its own candidate. The place is not part
        # of the description, it is a property of the photograph, so take it
        # from the live candidate list rather than re-tagging to acquire it.
        if photo and not photo.get("place"):
            for c in cands:
                if c.get("photo_id") == photo.get("photo_id") and c.get("place"):
                    photo = {**photo, "place": c["place"]}
                    break
        if photo and not (OUT_DIR / photo["file"]).exists():
            if not download_square(photo["url"], OUT_DIR / photo["file"]):
                missing_img += 1
                photo = None
        m = by_taxon.get(tid, [0] * 12)

        rec = {
            # Identity is the iNat taxon_id. The slug is a label, not a key:
            # two species here share the common name "Silver Trevally".
            "id": s["taxon_id"],
            "slug": slugs[str(s["taxon_id"])],
            "taxon_id": s["taxon_id"],
            "name": s["name"],
            "sci": s["sci"],
            "annual": s["annual"],
            "months": m,                       # RAW counts — normalise with `effort`
            # Seasonality is read at NSW scale, where it is actually visible;
            # the bay's own counts above stay the local abundance figure.
            "season": (((season or {}).get("species") or {}).get(str(s["taxon_id"])) or {})
                      .get("state"),
            "season_share": (((season or {}).get("species") or {}).get(str(s["taxon_id"])) or {})
                            .get("share"),
            "season_peak": (((season or {}).get("species") or {}).get(str(s["taxon_id"])) or {})
                           .get("peak"),
            "season_records": (((season or {}).get("species") or {}).get(str(s["taxon_id"])) or {})
                              .get("records"),
            "img": photo["file"] if photo else None,
            "credit": (f"{photo['who_name'] or photo['who']} ({photo['lic']})"
                       if photo else None),
            "observer": photo["who"] if photo else None,
            "licence": photo["lic"] if photo else None,
            "obs_url": (f"https://www.inaturalist.org/observations/{photo['obs_id']}"
                        if photo else None),
            "photo_place": (photo or {}).get("place"),
            # "unverified" means the identification is not community-confirmed.
            # The app must not present that as settled.
            "photo_tier": (photo or {}).get("tier"),
            "last_seen": photo.get("observed_on") if photo else None,
            "size_cm": (sizes.get(tid) or {}).get("cm"),
            "size_low_cm": (sizes.get(tid) or {}).get("cm_low"),
            "size_bucket": (sizes.get(tid) or {}).get("bucket"),
            "size_juvenile": (sizes.get(tid) or {}).get("buckets_juvenile") or [],
            "size_basis": (sizes.get(tid) or {}).get("basis"),
            "size_source": (sizes.get(tid) or {}).get("source"),
            "max_cm": (sizes.get(tid) or {}).get("max_cm"),
            "size_estimated": (sizes.get(tid) or {}).get("typical_estimated") or False,
            "size_measure": (sizes.get(tid) or {}).get("measure"),
            "gbif": (gbif or {}).get(tid),
            # Model knowledge, never an observation. The app must show it as
            # such — same treatment as an estimated size.
            "fact": ((facts or {}).get(tid) or {}).get("fact"),
            "fact_category": ((facts or {}).get(tid) or {}).get("category"),
            "fact_checked": bool(((facts or {}).get(tid) or {}).get("checked")),
            # Only verified sets ship. A wrong second appearance is worse than
            # none: it puts the species under a colour it never wears, which is
            # the same findability failure pointing the other way.
            # null = never tested against the bay, which is NOT the same as
            # tested and disagreed. The UI must treat it as unproven, not false.
            "season_bay_r": (agree or {}).get(tid),
            "morphs": (((morphs or {}).get(tid) or {}).get("morphs")
                       if ((morphs or {}).get(tid) or {}).get("checked") else None),
        }
        if t:
            rec.update({
                "shape": t.get("shape"),
                "colour": t.get("colour_primary"),
                "colour2": t.get("colour_secondary"),
                "finish": t.get("finish"),
                "tail": t.get("tail_shape"),
                "fin": t.get("fin_notable"),
                # Curated only. The sample settled this: asked to count fish in
                # frame, 34 of 40 said "one" — iNat photos are portraits, so the
                # count measured the photographer's framing, not the animal.
                # Behaviour cannot come from a still, so it comes from the file.
                "grouping": behaviour.get(s["sci"]),
                "grouping_source": "curated" if behaviour.get(s["sci"]) else None,
                "markings": t.get("markings") or [],
                "where": t.get("where"),
                "distinctive": t.get("distinctive"),
                "confidence": t.get("confidence"),
                "photo_caveat": t.get("photo_caveat"),
                "shape_from": t.get("shape_from"),
                "photo_ok": t.get("photo_ok"),
                "photo_shows": t.get("photo_shows"),
                "shape_contested": t.get("shape_contested", False),
            })
            if t.get("photo_ok") is False:
                review.append({"name": s["name"], "id": rec["id"],
                               "why": "photo may show " + (t.get("photo_shows") or "another animal"),
                               "img": rec["img"]})
            if t.get("confidence") != "high":
                review.append({"name": s["name"], "id": rec["id"],
                           "shape_votes": t.get("shape_votes"),
                           "slug": rec["slug"],
                               "confidence": t.get("confidence"),
                "photo_caveat": t.get("photo_caveat"),
                "shape_from": t.get("shape_from"),
                "photo_ok": t.get("photo_ok"),
                "photo_shows": t.get("photo_shows"),
                "shape_contested": t.get("shape_contested", False),
                               "img": rec["img"], "tags": t})
        fix = corrections.get(str(s["taxon_id"])) or {}
        for k, v in fix.items():
            if k.startswith("_"):
                continue
            rec[k] = v
        if fix:
            corrected += 1
            rec["hand_checked"] = True
            rec.pop("shape_contested", None)     # a person settled it
            # A hand-entered size must drag its bucket with it, or the detail
            # sheet says 30 cm while the filter still files it under 40.
            if "size_cm" in fix and fix["size_cm"]:
                cm = float(fix["size_cm"])
                rec["size_basis"] = fix.get("size_basis", "typical")
                rec["size_bucket"] = next(
                    (k for k, lo, hi, _ in SIZE_BUCKETS if lo <= cm < hi),
                    SIZE_BUCKETS[-1][0])
                rec["size_low_cm"] = max(1, round(cm * JUVENILE_FRACTION))
                rec["size_juvenile"] = [
                    k for k, lo, hi, _ in SIZE_BUCKETS
                    if rec["size_low_cm"] < hi and cm >= lo and k != rec["size_bucket"]]
        out.append(rec)

    out.sort(key=lambda r: -r["annual"])
    doc = {
        "provenance": prov or {},
        "generated_at": now.isoformat(timespec="seconds"),
        "source": {
            "place_id": PLACE_ID,
            "place": "Cabbage Tree Bay Aquatic Reserve",
            "records": sum(effort),
            "note": ("Species, counts and photographs from iNaturalist "
                     "observations inside the reserve. Attributes derived from "
                     "those photographs; size from FishBase " + FB_RELEASE + "."),
        },
        "effort": effort,                      # monthly observation totals, in the bay
        "season_effort": (season or {}).get("effort"),   # and across NSW
        "size_buckets": [{"key": k, "low_cm": lo, "high_cm": hi, "label": lb}
                         for k, lo, hi, lb in SIZE_BUCKETS],
        "species": out,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "ctbar.json").write_text(
        json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    (OUT_DIR / "ctbar-review.json").write_text(
        json.dumps(review, indent=1), encoding="utf-8")

    kb = (OUT_DIR / "ctbar.json").stat().st_size // 1024
    imgs = len(list(IMG_DIR.glob("*.webp"))) if IMG_DIR.exists() else 0
    img_kb = sum(p.stat().st_size for p in IMG_DIR.glob("*.webp")) // 1024 if imgs else 0

    log(f"6/6 emit         species/ctbar.json  {kb} KB, {len(out)} species")
    log(f"                 species/img/        {imgs} files, {img_kb} KB")
    print()
    log(f"  tagged           {len(tags)}")
    log(f"  no photo         {no_photo}   (no re-usable licence in the bay)")
    log(f"  untagged         {no_tag}   (photo too poor to describe)")
    if missing_img:
        log(f"  image unfetchable {missing_img}   (no picture, and no credit shown)")
    log(f"  hand-corrected   {corrected}   (tools/tag-overrides.json)")
    log(f"  no size          {no_size}   (unmatched in FishBase — renders grey)")
    log(f"  NEEDS REVIEW     {len(review)}   -> species/ctbar-review.json")
    if review:
        print()
        log("  Spot-check these before shipping. A wrong attribute makes a fish")
        log("  unfindable and the swimmer never learns why.")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    global MODEL, PRICE_IN, PRICE_OUT

    ap = argparse.ArgumentParser(description="Bake the Cabbage Tree Bay fish dataset.")
    ap.add_argument("--force", action="store_true",
                    help="ignore all caches and rebuild from scratch (re-pays for tagging)")
    ap.add_argument("--force-tags", action="store_true",
                    help="keep the iNat caches but re-run the vision pass")
    ap.add_argument("--limit", type=int,
                    help="only process the N most-observed species (for a dry run)")
    ap.add_argument("--typical", action="store_true",
                    help="look up TYPICAL adult length by web search where FishBase "
                         "gives only a maximum. Measured on the 102 with both: max "
                         "and typical fall in different buckets 62%% of the time.")
    ap.add_argument("--typical-limit", type=int, default=0, metavar="N",
                    help="only look up the N most-recorded (for a dry run)")
    ap.add_argument("--check-photos", action="store_true",
                    help="ask whether each photo actually shows the KIND of animal "
                         "it is filed under. Catches misidentified observations — "
                         "a crab filed as a sea slug — which no other check sees.")
    ap.add_argument("--colour", action="store_true",
                    help="re-read colour on white-balanced copies of the photos "
                         "whose red is badly suppressed. Water eats red first, so "
                         "a brown animal can be tagged grey; 34%% of these photos "
                         "are affected. The displayed photo is never altered.")
    ap.add_argument("--retire-facts", action="store_true",
                    help="drop facts that only restate the morph data, so the "
                         "same claim does not live in two places and drift")
    ap.add_argument("--best-photo", action="store_true",
                    help="have the vision pass choose the most legible candidate "
                         "per species instead of taking the best-voted one")
    ap.add_argument("--morphs", action="store_true",
                    help="find species whose sexes or juveniles look like "
                         "different animals, so the filter can match either")
    ap.add_argument("--facts", action="store_true",
                    help="look up one curiosity per species, then have the "
                         "stronger model try to refute the most-seen ones")
    ap.add_argument("--no-fact-check", action="store_true",
                    help="skip the adversarial pass over the top 150 facts")
    ap.add_argument("--name-shapes", action="store_true",
                    help="for species whose photographs gave no shape, ask by name "
                         "instead. A sea cucumber's body plan is a fact about the "
                         "species, not something to squint at a bad photo for.")
    ap.add_argument("--vote", action="store_true",
                    help="settle the shape axis by ballot: cast cheap shape-only "
                         "reads until each species has two that agree. Measured at "
                         "~$0.005 a ballot against $0.023 for a full re-tag.")
    ap.add_argument("--verify", action="store_true",
                    help="re-tag every species currently below 'high' confidence "
                         "under the stricter photo rule, then report where the "
                         "new answer disagrees with the old. Disagreement is a "
                         "far better review signal than self-reported confidence.")
    ap.add_argument("--sample", type=int, metavar="N",
                    help="tag a stratified sample of N species and print a "
                         "vocabulary profile, without emitting. Do this before "
                         "committing to the full run.")
    ap.add_argument("--skip-tags", action="store_true",
                    help="build without the vision pass — no API key needed, no attributes")
    ap.add_argument("--model", default=MODEL,
                    help=f"model for the vision pass (default {MODEL}). "
                         f"claude-sonnet-5 costs ~60%% less and this is a "
                         f"constrained perception task — spot-check before switching.")
    ap.add_argument("--price", nargs=2, type=float, metavar=("IN", "OUT"),
                    help="USD per million tokens for --model, if not Opus 5")
    args = ap.parse_args()

    MODEL = args.model
    if args.price:
        PRICE_IN, PRICE_OUT = args.price

    try:
        species = stage_species(args.force)
        if args.limit:
            species = species[:args.limit]
            log(f"    --limit {args.limit}: {len(species)} species this run")

        slugs = assign_slugs(species)
        months = stage_months(species, args.force)
        prov = bay_provenance(species)
        # needs months, so it runs after stage_months
        _vis = None
        season = stage_season(species, args.force)
        agree = season_bay_agreement(species, months, season["species"])
        for _s in species:
            _s["months"] = (months.get("by_taxon") or {}).get(str(_s["taxon_id"])) or [0]*12
        _vis = summer_visitors(species, season)
        prov["visitors"] = _vis
        prov["year"] = bay_year(species, _vis)
        photos = stage_photos(species, args.force, slugs)
        picks = cache_read("photo-picks.json") or {}
        if args.best_photo:
            picks = stage_best_photo(species, photos, args.force)
        # Reorder on EVERY run, not only when the choosing stage runs. Leaving
        # it inside the flag meant the tagger still walked from the best-voted
        # shot while the emit showed the chosen one, so 286 species were
        # described from a photograph nobody would see.
        for _tid, _v in (picks or {}).items():
            cl = photos.get(_tid) or []
            for _i, _c in enumerate(cl):
                if _c.get("photo_id") == _v.get("photo_id") and _i:
                    cl.insert(0, cl.pop(_i))
                    break
        gbif = stage_gbif_keys(species, args.force)
        sizes = stage_size(species, args.force)
        # The paid lookup is opt-in; folding in cached answers and the
        # hand-researched overrides is free, so it happens on every run. Gating
        # the overrides behind --typical meant a hand-checked size only reached
        # the app on a run that also spent money.
        typ = cache_read("typical.json") or {}
        if args.typical:
            typ = stage_typical(species, sizes, args.force, limit=args.typical_limit)
        typ = apply_typical_overrides(species, typ)
        if typ:
            sizes = stage_size(species, True, typical=typ)   # rebuild with it

        previous = {}
        if args.verify:
            allt = cache_read("tags.json") or {}
            previous = {k: v for k, v in allt.items()
                        if (v or {}).get("confidence") != "high"}
            cache_write("tags.json", {k: v for k, v in allt.items()
                                      if k not in previous})
            log(f"    --verify: re-tagging {len(previous)} hedged species "
                f"({len(allt) - len(previous)} high-confidence kept)")

        if args.skip_tags:
            log("5/6 tags         skipped (--skip-tags)")
            tags = cache_read("tags.json") or {}
        else:
            tags = stage_tags(species, photos, args.force or args.force_tags,
                              sample=args.sample, picks=picks)

        if args.check_photos and not args.skip_tags:
            tags = stage_plausible(species, tags)
        if args.colour and not args.skip_tags:
            tags = stage_colour(species, tags)
        if args.name_shapes and not args.skip_tags:
            tags = stage_shape_by_name(species, tags)
        # Applied on every run, from its own store, before the hand overrides.
        # Precedence: a person's ruling, then the species, then the photograph.
        _named = cache_read("name-shapes.json") or {}
        for _tid, _v in _named.items():
            rec = tags.setdefault(_tid, {"shape_only": True})
            rec["shape"] = _v["shape"]
            rec["shape_from"] = _v.get("from", "species knowledge, not the photograph")
            if _v.get("was_photo"):
                rec["shape_was_photo"] = _v["was_photo"]
            rec.pop("shape_contested", None)
        if _named:
            log(f"5e/6 shape store  {len(_named)} shapes applied from name-shapes.json")
        facts = cache_read("facts.json") or {}
        if args.facts:
            facts = stage_facts(species, args.force,
                                refute=not args.no_fact_check)
        # Applied on every run, paid stage or not: a hand ruling must not need
        # an API call to reach the app.
        facts = apply_fact_overrides(species, facts)
        morphs = cache_read("morphs.json") or {}
        if args.morphs:
            morphs = stage_morphs(species, args.force)
        # Applied on every run, paid stage or not: a hand ruling must not need
        # an API call to reach the app.
        morphs = apply_morph_overrides(species, morphs)
        if args.retire_facts:
            facts = stage_retire_facts(species, facts, morphs)
            facts = apply_fact_overrides(species, facts)   # a ruling still wins
        if args.vote and not args.skip_tags:
            tags = stage_vote(species, tags)
        if previous:
            report_disagreement(previous, tags, species)
        report_distribution(tags, sizes, species)
        if args.sample:
            print()
            log("Sample run — vocabulary profile above. Freeze the enums in")
            log("FishTags before the full pass; changing them later re-tags")
            log("everything AND invalidates the human review.")
            return 0
        stage_emit(species, months, photos, tags, sizes, slugs, season,
                   facts=facts, morphs=morphs, agree=agree,
                   picks=picks, prov=prov, gbif=gbif)
        print()
        log("Done. Add species/ctbar.json and species/img/ to the service-worker")
        log("precache, then commit from the repo clone — never from the Drive copy.")
        return 0

    except KeyboardInterrupt:
        log("interrupted — caches kept, re-run to resume")
        return 130
    except Exception as e:                # noqa: BLE001
        log(f"FAILED: {e}")
        notify_failure(str(e))
        raise


if __name__ == "__main__":
    sys.exit(main())
