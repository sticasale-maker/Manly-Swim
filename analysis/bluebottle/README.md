# Bluebottle analysis (July 2026 originals + 9 Oct 2026 checks)

The original scripts behind the BBF_ constants, recovered from `C:\FBExtract\inatpull` on
9 Oct 2026. Summary of what they show is in `docs/bluebottle-model.md` §5 and §5a.

- `Bluebottle_phase1.py`: regional phase-1 fit (lag sweep, aspect, season, GBM). Background days at 08:00.
- `Bay_check.py`: the source of the old bay "AUC 0.89". Controls at a fixed 08:00 while sightings
  keep their observation time; with that fixed it gives 0.78.
- `bb_wind_join.py` -> `bb_windjoined.csv`: matched case-control set (3 controls/case, ERA5 per beach).
- `bb_aspect_refit.py`: year-blocked held-out harness (folds = case year). Aspect question settled.
- `month_term.py` (9 Oct): month/abundance terms in the same folds. No held-out lift at the bay.

Run `python bb_aspect_refit.py` / `python month_term.py` from this folder (pandas, numpy, scikit-learn).
