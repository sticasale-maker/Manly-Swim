# Bluebottle analysis scripts

The July 2026 scripts behind the BBF_ constants, plus the 9 Oct 2026 month-term test.
Current figures are in `docs/bluebottle-model.md` §5.

- `Bluebottle_phase1.py`: regional phase-1 fit (lag sweep, aspect, season, GBM).
- `Bay_check.py`: bay-only cosine vs fetch-weighting comparison.
- `bb_wind_join.py` -> `bb_windjoined.csv`: matched case-control set (3 controls/case, ERA5 per beach).
- `bb_aspect_refit.py`: year-blocked held-out harness (folds = case year).
- `month_term.py`: month/abundance terms in the same folds. No held-out lift at the bay.

Run `python bb_aspect_refit.py` / `python month_term.py` from this folder (pandas, numpy, scikit-learn).
