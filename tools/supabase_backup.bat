@echo off
REM ---------------------------------------------------------------------------
REM supabase_backup.bat - nightly Supabase snapshot for Manly Swim.
REM
REM The Scheduled Task runs THIS, not the .py, so the log and the exit code land
REM in one place. Same shape as water_temp_morning.bat.
REM
REM Create the task once (run this line in a terminal, not in the scheduler GUI):
REM   schtasks /create /tn "Manly Swim Supabase backup" /sc daily /st 03:30 /f ^
REM     /tr "C:\Users\stica\repos\Manly-Swim\tools\supabase_backup.bat"
REM
REM The service_role key is read from C:\Users\stica\.viz-secrets\supabase_service_key.txt
REM That folder is deliberately outside Google Drive: the key must not sync.
REM ---------------------------------------------------------------------------
setlocal

set "PY=C:\Users\stica\AppData\Local\Python\bin\python.exe"
set "SCRIPT=%~dp0supabase_backup.py"
set "BACKUPS=C:\Users\stica\Google Drive\VIZ\APPS\Manly swim\supabase-backups"

if not exist "%BACKUPS%" mkdir "%BACKUPS%"

REM Keep the previous run's log so a failure can be compared with the last good run.
if exist "%BACKUPS%\last-run.log" move /y "%BACKUPS%\last-run.log" "%BACKUPS%\previous-run.log" >nul

echo ===== %DATE% %TIME% ===== > "%BACKUPS%\last-run.log"
"%PY%" "%SCRIPT%" >> "%BACKUPS%\last-run.log" 2>&1
set RC=%ERRORLEVEL%

REM The script pushes its own ntfy alert with the detail. This only catches the
REM case where python itself could not start, which the script cannot report.
if %RC% GEQ 2 (
    curl -s -H "Title: Manly Swim backup: script did not run" -H "Priority: high" ^
         -H "Tags: warning,floppy_disk" ^
         -d "supabase_backup.bat could not run python (exit %RC%). Check %BACKUPS%\last-run.log" ^
         https://ntfy.sh/FBscrapefailed >nul
)

echo Exit code %RC% >> "%BACKUPS%\last-run.log"
exit /b %RC%
