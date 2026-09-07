@echo off
REM ---------------------------------------------------------------------------
REM  Double-click this to review the species data.
REM
REM  The review pages read species/ctbar.json with fetch(), and a browser
REM  refuses that over file:// -- so opening the .html directly gives a page
REM  with no data in it and no obvious reason why. That has now caught someone
REM  twice. This serves the repo over HTTP and opens the right URL, which is the
REM  only way those pages ever worked.
REM
REM  Ctrl-C, or just close this window, when you have finished.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0.."

set PORT=8765
where python >nul 2>nul
if errorlevel 1 (
  echo Python is not on PATH. Install it, or serve the repo some other way and
  echo open http://localhost:%PORT%/tools/morph-review.html by hand.
  pause
  exit /b 1
)

echo.
echo   Serving %CD%
echo.
echo   Morphs  http://localhost:%PORT%/tools/morph-review.html
echo   Shapes  http://localhost:%PORT%/tools/review.html
echo   The app http://localhost:%PORT%/species.html
echo.
echo   Close this window when you are done.
echo.

start "" "http://localhost:%PORT%/tools/morph-review.html"
python -m http.server %PORT%
