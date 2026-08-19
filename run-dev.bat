@echo off
REM Start all three Shruti processes, each in its own window.
REM
REM   run-dev.bat
REM
REM They are separate windows on purpose: each one has its own log, and killing
REM the site must not take down a rag_core that spent 12 seconds loading a
REM 655 MB index.
REM
REM THE SITE IS frontends\, SERVED STATICALLY.
REM It replaced the Next.js app that used to live in apps\web, which has been
REM removed; see HANDOFF.md. There is no build step and no node_modules. The
REM page is plain HTML, one stylesheet and ES modules, so the server is
REM python -m http.server and a change is visible on reload.
REM
REM PYTHONIOENCODING is set on both Python services because printing a Hindi
REM transcript to a Windows console raises UnicodeEncodeError on cp1252, which
REM kills the request rather than just garbling the log line.

setlocal
set ROOT=%~dp0
set PY=%ROOT%.venv\Scripts\python.exe

if not exist "%PY%" (
  echo .venv not found at %PY%
  echo Create it first:  py -3.12 -m venv .venv
  exit /b 1
)

if not exist "%ROOT%.env" (
  echo WARNING: no .env found. Voice and the generative path will not work.
  echo Copy .env.example to .env and fill in SARVAM_API_KEY and GROQ_API_KEY.
  echo.
)

if not exist "%ROOT%artifacts\indexes\c1\index.bin" (
  echo artifacts\indexes\c1\index.bin is missing - rag_core cannot start.
  echo Rebuild it first; see HANDOFF.md section 2.3. Takes about 45 minutes.
  exit /b 1
)

echo Starting rag_core on :8000  ^(loads a 655 MB index, allow ~12s^)
start "shruti rag_core" cmd /k "cd /d %ROOT%services && set PYTHONIOENCODING=utf-8 && %PY% -m uvicorn rag_core.main:app --port 8000"

echo Starting stt_gateway on :8001
start "shruti stt_gateway" cmd /k "cd /d %ROOT%services && set PYTHONIOENCODING=utf-8 && %PY% -m uvicorn stt_gateway.main:app --port 8001"

echo Starting the site on :3000
start "shruti site" cmd /k "cd /d %ROOT%frontends && %PY% -m http.server 3000"

echo.
echo   Open http://localhost:3000
echo.
echo   Port 3000 is not a preference. stt_gateway allows CORS from :3000 only,
echo   because it holds the Sarvam key. On any other port typing works and
echo   speaking fails with a CORS error that reads like a broken microphone.
echo.
echo   Use localhost, NOT your LAN IP. The microphone needs a secure origin;
echo   localhost counts as one and 192.168.x.x does not, so on a LAN address
echo   the mic silently never prompts.
echo.
echo   Check http://localhost:8000/health before testing - it reports whether
echo   the reranker and the passage store actually loaded.
endlocal
