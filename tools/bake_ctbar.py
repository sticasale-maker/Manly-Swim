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

def fetch_photo_candidates(taxon_id: int, n: int = 3) -> list:
    """Best CC-licensed photos of this species taken INSIDE the reserve.
    Ordered by community votes, so the clearest shots come first."""
    d = inat("observations", place_id=PLACE_ID, taxon_id=taxon_id,
             photo_license=PHOTO_LICENCES, quality_grade="research",
             order_by="votes", per_page=n)
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
            })
    return out


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

    log(f"3/6 photos       {len(todo)} to fetch ({len(out)} cached)")
    for i, s in enumerate(todo, 1):
        cands = fetch_photo_candidates(s["taxon_id"])
        if not cands:
            seen_none.add(str(s["taxon_id"]))   # remember the miss, don't re-ask
            continue
        slug = slugs[str(s["taxon_id"])]
        # Keep every candidate: if the vision pass rejects the first as
        # unusable we retry with the next rather than dropping the species.
        for c in cands:
            c["file"] = f"img/{slug}-{c['photo_id']}.webp"
        out[str(s["taxon_id"])] = cands
        if i % 25 == 0:
            log(f"    {i}/{len(todo)} scanned")
            cache_write("photos.json", {**out, "_none": sorted(seen_none)})

    cache_write("photos.json", {**out, "_none": sorted(seen_none)})
    log(f"3/6 photos       {len(out):>4} species have a re-usable in-bay photo"
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
SIZE_BUCKETS = [
    ("hand", 0, 15, "hand-sized"),
    ("forearm", 15, 35, "forearm"),
    ("arm", 35, 70, "arm's length"),
    ("you", 70, 150, "as long as you"),
    ("bigger", 150, 10_000, "bigger than you"),
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
                cands = by_epithet.get(sci.split()[-1], [])
                if len(cands) == 1:            # unique across all of FishBase
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


def stage_typical(species: list, sizes: dict, force: bool, limit: int = 0,
                  min_cm: float = 40.0, retry: bool = True) -> dict:
    """Look up typical length where it will actually change a size bucket.

    Measured: max-vs-typical puts a species in a different bucket 85% of the
    time above 80 cm, 70% at 40-80 cm, but only ~35% below 40 cm — where a 12 cm
    maximum and an 8 cm typical are both "hand-sized" anyway. So the lookup is
    filtered to animals big enough for the distinction to matter.
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


def stage_tags(species: list, photos: dict, force: bool, sample: int = 0) -> dict:
    tags = {} if force else (cache_read("tags.json") or {})

    # Drop anything the current vocabulary can no longer express, so an enum
    # change re-tags exactly the species it affects and no others.
    import collections
    stale = collections.Counter()
    for tid in list(tags):
        why = _stale_against_schema(tags[tid] or {})
        if why:
            stale[why] += 1
            del tags[tid]
    if stale:
        log("    retired vocabulary, re-tagging: "
            + ", ".join(f"{v}x {k}" for k, v in stale.most_common(5)))
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
                t = tag_one(client, s["name"], img)
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
            if done % 20 == 0:
                log(f"    {done}/{len(todo)} tagged")
                cache_write("tags.json", tags)      # checkpoint; API calls cost money

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
               sizes: dict, slugs: dict) -> None:
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
    no_size = sum(1 for s in species if str(s["taxon_id"]) not in sizes)
    for s in species:
        tid = str(s["taxon_id"])
        t = tags.get(tid)
        cands = photos.get(tid)
        if not cands:
            no_photo += 1
        if not t:
            no_tag += 1

        photo = (t or {}).get("photo") or (cands[0] if cands else None)
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
            "img": photo["file"] if photo else None,
            "credit": (f"{photo['who_name'] or photo['who']} ({photo['lic']})"
                       if photo else None),
            "observer": photo["who"] if photo else None,
            "licence": photo["lic"] if photo else None,
            "obs_url": (f"https://www.inaturalist.org/observations/{photo['obs_id']}"
                        if photo else None),
            "last_seen": photo.get("observed_on") if photo else None,
            "size_cm": (sizes.get(tid) or {}).get("cm"),
            "size_low_cm": (sizes.get(tid) or {}).get("cm_low"),
            "size_bucket": (sizes.get(tid) or {}).get("bucket"),
            "size_juvenile": (sizes.get(tid) or {}).get("buckets_juvenile") or [],
            "size_basis": (sizes.get(tid) or {}).get("basis"),
            "size_source": (sizes.get(tid) or {}).get("source"),
            "max_cm": (sizes.get(tid) or {}).get("max_cm"),
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
        out.append(rec)

    out.sort(key=lambda r: -r["annual"])
    doc = {
        "generated_at": now.isoformat(timespec="seconds"),
        "source": {
            "place_id": PLACE_ID,
            "place": "Cabbage Tree Bay Aquatic Reserve",
            "records": sum(effort),
            "note": ("Species, counts and photographs from iNaturalist "
                     "observations inside the reserve. Attributes derived from "
                     "those photographs; size from FishBase " + FB_RELEASE + "."),
        },
        "effort": effort,                      # monthly observation totals
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
        photos = stage_photos(species, args.force, slugs)
        sizes = stage_size(species, args.force)
        if args.typical:
            typ = stage_typical(species, sizes, args.force, limit=args.typical_limit)
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
                              sample=args.sample)

        if args.check_photos and not args.skip_tags:
            tags = stage_plausible(species, tags)
        if args.colour and not args.skip_tags:
            tags = stage_colour(species, tags)
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
        stage_emit(species, months, photos, tags, sizes, slugs)
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
