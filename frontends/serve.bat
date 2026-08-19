@echo off
REM ============================================================================
REM  Shruti, served.
REM
REM    frontends\serve.bat
REM
REM  Brings up the two Python services and the static server that holds the
REM  site, then opens it. Already-running processes are detected and left
REM  alone, so re-running this never costs you a warm index.
REM
REM  This used to be a switcher between eight interface treatments. Seven of
REM  them were exploratory and have been removed; what is left IS the site, and
REM  it is served from the root of this directory.
REM
REM  WHY PORT 3000 IS NOT CONFIGURABLE HERE
REM  services\stt_gateway\config.py allows CORS from localhost:3000 and
REM  127.0.0.1:3000 only, because that service holds the Sarvam API key and a
REM  wildcard origin on a credential-holding service is not acceptable. Serve
REM  this page anywhere else and typing works while speaking fails, with a CORS
REM  rejection that looks exactly like a broken microphone.
REM
REM  The microphone additionally needs a secure origin. localhost counts as one;
REM  a LAN address like 192.168.x.x does not, and the mic will silently never
REM  prompt. Always open localhost, never the LAN IP.
REM ============================================================================

setlocal enabledelayedexpansion

set "FRONT=%~dp0"
set "ROOT=%FRONT%.."
set "PY=%ROOT%\.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo.
  echo   .venv not found at %PY%
  echo   Create it first:  py -3.12 -m venv .venv
  echo.
  pause
  exit /b 1
)

echo.
echo   Shruti
echo   ------

REM --- rag_core -------------------------------------------------------------
call :isup 8000
if "!UP!"=="1" (
  echo   rag_core      already running on :8000
) else (
  if not exist "%ROOT%\artifacts\indexes\c1\index.bin" (
    echo   rag_core      SKIPPED, artifacts\indexes\c1\index.bin is missing.
    echo                 Rebuild it first, see HANDOFF.md section 2.3.
    echo                 Text and voice queries will fail until you do.
  ) else (
    echo   rag_core      starting on :8000, loads a 655 MB index, allow ~12s
    start "shruti rag_core" cmd /k "cd /d "%ROOT%\services" && set PYTHONIOENCODING=utf-8 && "%PY%" -m uvicorn rag_core.main:app --port 8000"
  )
)

REM --- stt_gateway ----------------------------------------------------------
call :isup 8001
if "!UP!"=="1" (
  echo   stt_gateway   already running on :8001
) else (
  if not exist "%ROOT%\.env" (
    echo   stt_gateway   starting on :8001, but no .env found.
    echo                 Voice will fail until SARVAM_API_KEY is set. Typing still works.
  ) else (
    echo   stt_gateway   starting on :8001
  )
  start "shruti stt_gateway" cmd /k "cd /d "%ROOT%\services" && set PYTHONIOENCODING=utf-8 && "%PY%" -m uvicorn stt_gateway.main:app --port 8001"
)

REM --- static server --------------------------------------------------------
call :isup 3000
if "!UP!"=="1" (
  echo   site          already served on :3000
) else (
  echo   site          serving on :3000
  start "shruti site :3000" cmd /k "cd /d "%FRONT%" && "%PY%" -m http.server 3000"
)

echo.
echo   Opening http://localhost:3000/
start "" "http://localhost:3000/"

echo.
echo   Site         http://localhost:3000/
echo   How it works http://localhost:3000/docs.html
echo   Health       http://localhost:8000/health
echo.
echo   Every server keeps running in its own window. Close the window to stop it.
echo.
endlocal
exit /b 0

REM ----------------------------------------------------------------------------
REM  isup <port>  ->  UP=1 when something is LISTENING on that port.
REM  The trailing space in the pattern stops :3000 matching :13000.
REM ----------------------------------------------------------------------------
:isup
set "UP=0"
for /f "tokens=*" %%L in ('netstat -ano -p TCP ^| findstr /r /c:":%~1 .*LISTENING"') do set "UP=1"
exit /b 0
