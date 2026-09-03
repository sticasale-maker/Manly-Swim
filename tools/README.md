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

Everything keys on iNaturalist taxon id, so a re-run only pays for what is
genuinely new — about 30 species a year.

## The overrides outrank the model

`size-overrides.json`, `behaviour-overrides.json` and `tag-overrides.json` are
read *after* the vision pass and *before* emit. A hand decision is never in the
blast radius of a re-bake, a vocabulary change or a re-tag.
