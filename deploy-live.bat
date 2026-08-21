@echo off
REM Push the current build to the LIVE site, https://shrutirag.duckdns.org
REM
REM   deploy-live.bat          everything: pull on the box, sync the site, restart services
REM   deploy-live.bat site     static files only. No service restart, so no 12s index reload.
REM   deploy-live.bat copy     scp the files up instead of git pull. Use if the box
REM                            cannot authenticate to the private repo.
REM   deploy-live.bat check    touch nothing. Just report what the live site is serving.
REM
REM THIS IS THE COUNTERPART TO run-dev.bat. That one starts the three processes
REM locally; this one puts the same tree on the box that serves the public URL.
REM
REM DO NOT USE `gcloud compute ssh` HERE. On this Windows box it shells out to
REM PuTTY's plink and the server refuses the key, which looks like a permissions
REM problem and is not. Plain OpenSSH works and is what this uses. Windows 10
REM ships ssh.exe, scp.exe and curl.exe in System32, so there is nothing to
REM install.
REM
REM WHAT ACTUALLY GOES WRONG, and why this script verifies over HTTPS at the end:
REM the repo on the box lives in /home/haziqlandge/app but Caddy's web root is
REM /var/www/shruti. A home directory is 0750 and the caddy user cannot traverse
REM it, so editing the repo changes NOTHING until the rsync runs - and the
REM failure is silent. Checking the file you just copied proves nothing. The only
REM check that means anything is fetching the asset the browser fetches.

setlocal
set ROOT=%~dp0
set BOX=haziqlandge@34.100.222.236
set KEY=%USERPROFILE%\.ssh\google_compute_engine
set URL=https://shrutirag.duckdns.org
set MODE=%~1
if "%MODE%"=="" set MODE=all

if not exist "%KEY%" (
  echo SSH key not found at %KEY%
  echo That is the key gcloud generated. If it is elsewhere, edit KEY above.
  exit /b 1
)

if "%MODE%"=="check" goto :verify

REM --------------------------------------------------------------------------
REM Local preflight. The box pulls from GitHub, so anything not pushed does not
REM exist as far as this deploy is concerned. Catch that here rather than after
REM a successful-looking deploy that shipped nothing.
REM --------------------------------------------------------------------------
if not "%MODE%"=="copy" (
  git -C "%ROOT%." diff --quiet
  if errorlevel 1 (
    echo.
    echo WARNING: you have uncommitted changes. The box pulls from GitHub, so
    echo they will NOT be deployed. Commit and push first, or use:
    echo     deploy-live.bat copy
    echo.
    choice /c YN /m "Deploy anyway"
    if errorlevel 2 exit /b 1
  )
  for /f %%B in ('git -C "%ROOT%." rev-parse --abbrev-ref HEAD') do set BRANCH=%%B
  git -C "%ROOT%." diff --quiet "@{upstream}" HEAD 2>nul
  if errorlevel 1 (
    echo.
    echo WARNING: local commits are not pushed. Run:  git push
    echo.
    choice /c YN /m "Deploy anyway"
    if errorlevel 2 exit /b 1
  )
)

echo.
echo == deploying to %BOX%

if "%MODE%"=="copy" goto :copy

REM --------------------------------------------------------------------------
REM Normal path: the box pulls, then runs the script that lives in the repo, so
REM the deploy logic is versioned with the thing it deploys.
REM --------------------------------------------------------------------------
if "%MODE%"=="site" (
  ssh -i "%KEY%" %BOX% "cd ~/app && git pull --ff-only && chmod +x deploy/deploy.sh && ./deploy/deploy.sh --site"
) else (
  ssh -i "%KEY%" %BOX% "cd ~/app && git pull --ff-only && chmod +x deploy/deploy.sh && ./deploy/deploy.sh"
)
if errorlevel 1 (
  echo.
  echo Remote deploy failed. If it was `git pull` asking for credentials, the box
  echo cannot read the private repo. Use this instead, which needs no auth there:
  echo     deploy-live.bat copy
  exit /b 1
)
goto :verify

REM --------------------------------------------------------------------------
REM copy: send the working tree up over SSH instead of pulling. Slower, but it
REM needs no GitHub credentials on the box and it deploys what is on THIS disk,
REM including uncommitted work. tar over ssh rather than scp -r so the transfer
REM is one round trip and file modes survive.
REM --------------------------------------------------------------------------
:copy
echo    copying frontends/ and services/ from this machine
tar -cf - -C "%ROOT%." frontends services | ssh -i "%KEY%" %BOX% "tar -xf - -C ~/app"
if errorlevel 1 (
  echo Copy failed.
  exit /b 1
)
if "%MODE%"=="copy" (
  ssh -i "%KEY%" %BOX% "cd ~/app && chmod +x deploy/deploy.sh && ./deploy/deploy.sh"
)

REM --------------------------------------------------------------------------
REM Verify against the PUBLIC URL, by HASH rather than by grep.
REM
REM The first version of this check grepped each fetched asset for a phrase from
REM the newest change, and it reported "ok" for ui.js while the box was serving a
REM build two days old - the phrase it picked was already in the file before that
REM change. A probe that can pass against the wrong build is worse than no probe,
REM because it sends you hunting for a browser cache problem you do not have.
REM verify-live.ps1 compares the whole file, with line endings normalised so the
REM CRLF checkout here and the LF checkout on the box are not reported as a
REM permanent mismatch.
REM --------------------------------------------------------------------------
:verify
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%deploy\verify-live.ps1"
endlocal
