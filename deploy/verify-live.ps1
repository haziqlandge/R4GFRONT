# Is the live site actually serving what is in this working tree?
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File deploy\verify-live.ps1
#
# WHY A HASH AND NOT A GREP. The first version of this check grepped the fetched
# asset for a phrase from the newest change. It reported "ok" for ui.js while the
# box was serving a build two days old, because the phrase it happened to pick -
# "external source" - was already in the file before that change. A probe that
# can pass against the wrong build is worse than no probe: it sends you looking
# for a browser cache problem you do not have. Comparing the whole file cannot
# do that.
#
# LINE ENDINGS ARE NORMALISED FIRST. This repo checks out CRLF on Windows and LF
# on the Linux box, so the same file has two different hashes on disk and a raw
# byte comparison would report every file stale forever. Both sides are collapsed
# to LF before hashing, which compares content rather than checkout policy.

param(
  [string]$Url  = "https://shrutirag.duckdns.org",
  [string]$Root = (Join-Path $PSScriptRoot ".." | Resolve-Path).Path
)

$assets = @(
  "index.html",
  "docs.html",
  "console.js",
  "theme.css",
  "_shared/app.js",
  "_shared/core.js",
  "_shared/ui.js",
  "_shared/data.js",
  "_shared/docs.js",
  "_shared/base.css"
)

function Get-NormalisedHash([string]$text) {
  $lf = $text -replace "`r`n", "`n"
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    ($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($lf)) |
      ForEach-Object { $_.ToString("x2") }) -join ""
  } finally { $sha.Dispose() }
}

Write-Output "== $Url against $Root\frontends"
$stale = 0
$ok    = 0

foreach ($a in $assets) {
  $local = Join-Path (Join-Path $Root "frontends") ($a -replace "/", "\")
  if (-not (Test-Path $local)) { Write-Output ("   ?       {0}  not in repo" -f $a); continue }

  $lh = Get-NormalisedHash (Get-Content -LiteralPath $local -Raw -Encoding UTF8)

  try {
    # Cache-buster on the request so a proxy between here and Caddy cannot
    # answer with the same stale copy the browser is being given.
    # .Contains, NOT -like "*?*". In -like, "?" is the single-character
    # wildcard, so that test matches every non-empty string and this always
    # picked "&" - producing "index.html&v=..." and a 404 on every asset.
    $sep = if ($a.Contains("?")) { "&" } else { "?" }
    $resp = Invoke-WebRequest -Uri ("{0}/{1}{2}v={3}" -f $Url, $a, $sep, [guid]::NewGuid()) `
              -UseBasicParsing -TimeoutSec 25 -Headers @{ "Cache-Control" = "no-cache" } -ErrorAction Stop
    $rh = Get-NormalisedHash $resp.Content
  } catch {
    Write-Output ("   ERR     {0}  {1}" -f $a, $_.Exception.Message)
    $stale++
    continue
  }

  if ($lh -eq $rh) { Write-Output ("   ok      {0}" -f $a); $ok++ }
  else             { Write-Output ("   STALE   {0}" -f $a); $stale++ }
}

# The backend is versioned separately - services/ runs from the repo on the box
# and only a restart picks it up - so it gets its own line rather than being
# folded into the static count.
Write-Output ""
try {
  $h = Invoke-WebRequest -Uri "$Url/api/core/health" -UseBasicParsing -TimeoutSec 20 -ErrorAction Stop
  if ($h.Content -match '"aside"') { Write-Output "   ok      rag_core carries the aside rate limit" }
  else { Write-Output "   OLD     rag_core predates the aside rate limit - needs systemctl restart shruti-core" }
} catch {
  Write-Output ("   ERR     /api/core/health  {0}" -f $_.Exception.Message)
}

Write-Output ""
if ($stale -eq 0) {
  Write-Output "   $ok of $($assets.Count) assets match. The live site is this build."
  Write-Output "   If the page still looks old in a browser, that is the browser cache and"
  Write-Output "   not the deploy - Caddy sends no cache-control on these files. Ctrl-F5."
  exit 0
} else {
  Write-Output "   $stale asset(s) do not match. The box is serving a different build."
  Write-Output "   Run:  deploy-live.bat        (or ssh in and run deploy/deploy.sh)"
  exit 1
}
