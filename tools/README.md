# Bake tooling for *Who Lives Here*

    python tools/bake_ctbar.py            # build/refresh species/ctbar.json
    python tools/bake_ctbar.py --sample 40    # profile the vocabulary, spend ~$0.70
    python -m http.server 8000            # then open /tools/review.html

## Running a stage in isolation

Point the cache somewhere disposable. **Never call a stage with a subset of
species against the real cache** — stages write the whole `tags` dict back, so
a subset silently replaces everything:

    BAKE_CACHE_DIR=/tmp/bake-test python your_experiment.py

`cache_write` now refuses a write that loses more than half the entries, but
the isolation is the real protection; the guard is a backstop.

## What costs money

Only the vision stages. Measured, not estimated:

| stage | flag | per species | notes |
|---|---|---|---|
| tagging | *(default)* | $0.023 | full attribute set, Opus 5 |
| shape ballot | `--vote` | $0.0055 | one enum, tiny schema |
| colour re-read | `--colour` | $0.0055 | **reverted — see the note in the source** |
| photo check | `--check-photos` | $0.0055 | flags photos showing the wrong animal |
| typical size | `--typical` | $0.021 | Haiku + web search, cited |
| shape by name | `--name-shapes` | $0.0055 | asks what the animal *is*, not what the photo shows |
| curiosity | `--facts` | $0.0086 | Haiku; ~40% return one, the rest decline |
| fact check | with `--facts` | ~$0.037 | Opus tries to refute every fact; see below |
| morphs | `--morphs` | $0.0101 | second appearances (sex, juvenile), then refuted |
| retire facts | `--retire-facts` | $0.0066 | drops prose the morph data already says |

Everything keys on iNaturalist taxon id, so a re-run only pays for what is
genuinely new — about 30 species a year.

## A fact ships only if it survives a refutation

`--facts` writes one curiosity per species where one honestly exists, then a
stronger model is asked to *disprove* each of them. A claim that is refuted is
dropped, not softened; a check that errors leaves `checked` unset, and the app
shows nothing rather than something unverified. This is not belt-and-braces —
in the first sample, two of three facts about the most-watched species in the
bay were confidently wrong (Blue Gropers were said to guard nests; both they
and Old Wife are broadcast spawners).

Two traps, both paid for once already:

- **Never put `max_length` on a field the model fills.** It does not make the
  answer shorter, it makes a long answer fail validation and discards a call
  you have already been billed for. Ask for brevity in the prompt and bound
  the response with `max_tokens`.
- **Every paid stage checks for a key before it starts** (`_require_key`).
  Without that, a missing `ANTHROPIC_API_KEY` produced 583 identical
  `TypeError`s, resolved nothing, and still printed a coverage table and
  "Done" — a run that looked clean and had done no work at all.

## Two review pages, and why the first one is empty

    python -m http.server 8000     # from the REPO CLONE, not the Drive copy

- `tools/review.html` — shape/tag review. **Currently empty, and correctly so.**
  It returns priority 0 for any species whose shape came from the name rather
  than a photograph, and that is now all 640. Do not read an empty queue as a
  broken page.
- `tools/morph-review.html` — the 93 species carrying a second appearance,
  most-seen first. This is the one worth a person's time: a wrong morph files a
  species under a colour it never wears, which is the exact failure the feature
  exists to prevent, pointing the other way. Rulings download as
  `tools/morph-overrides.json`; re-running `python tools/bake_ctbar.py` with no
  flags is enough to apply them, no API calls.

The eleven species whose morphs were independently corroborated by their own
verified facts are parked at the END of that queue and badged, rather than
skipped — two model passes agreeing is evidence, not proof.

## The overrides outrank the model

`size-overrides.json`, `behaviour-overrides.json`, `typical-overrides.json`,
`fact-overrides.json`, `morph-overrides.json` and `tag-overrides.json` are
read *after* the vision pass and *before* emit. A hand decision is never in the
blast radius of a re-bake, a vocabulary change or a re-tag.

`typical-overrides.json` holds hand-researched *typical* sizes keyed by
scientific name, with the measurement named (`shell length`, `test diameter`,
`arm span`) because a sea star's 15 cm and a chiton's 15 cm are not the same
measurement. Entries marked `estimated` carry no source that states a typical
size and the app labels them as estimates. They are applied on every run — a
hand decision must not need a paid stage to reach the app.
