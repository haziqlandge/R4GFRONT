#!/usr/bin/env bash
#
# Deploy Shruti on the GCP box. RUN THIS ON THE BOX, not from a laptop.
#
#     ssh -i ~/.ssh/google_compute_engine haziqlandge@34.100.222.236
#     cd ~/app && git pull && ./deploy/deploy.sh
#
# WHY THIS FILE EXISTS. /etc/caddy/Caddyfile has said "deploy.sh syncs it" since
# Phase 7 and there was no deploy.sh - the sync was done by hand, and the two
# times it was skipped the site kept serving the previous build with no error
# anywhere. DONT-FORGET.md 12A records that failure; this closes it.
#
# THE TRAP IT EXISTS FOR. The repo lives in /home/haziqlandge/app and Caddy's web
# root is /var/www/shruti. A home directory is 0750, so the caddy user cannot
# traverse into it and serving from ~ returns 403 with nothing useful logged.
# Editing ~/app/frontends therefore changes NOTHING until this rsync runs. The
# failure is silent and convincing: scp succeeds, grep on the file in ~/app finds
# the new value, and the site keeps serving the old one.
#
#     ./deploy/deploy.sh              site + services
#     ./deploy/deploy.sh --site       static files only, no service restart
#     ./deploy/deploy.sh --services   restart only, no file sync
#
set -euo pipefail

REPO="${REPO:-$HOME/app}"
WEBROOT="${WEBROOT:-/var/www/shruti}"
HOST="${HOST:-https://shrutirag.duckdns.org}"

DO_SITE=1
DO_SERVICES=1
case "${1:-}" in
  --site)     DO_SERVICES=0 ;;
  --services) DO_SITE=0 ;;
  "")         ;;
  *) echo "usage: $0 [--site|--services]" >&2; exit 2 ;;
esac

# Refuse to run anywhere the paths do not exist, rather than rsyncing a laptop's
# frontends directory into a directory that happens to share a name.
[[ -d "$REPO/frontends" ]] || { echo "no $REPO/frontends - is this the box?" >&2; exit 1; }

# Tolerated, not required. ~/app on the box is NOT a git checkout - the tree got
# there by scp during Phase 7 and never by clone - so `set -e` plus a bare
# rev-parse aborted this script before it did any work. The deploy does not need
# git; this block is provenance when it happens to be available.
echo "== repo"
if git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
  echo "   branch $(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
  echo "   head   $(git -C "$REPO" log --oneline -1)"
else
  echo "   not a git checkout - deploying the tree as it stands on disk"
fi

if (( DO_SITE )); then
  echo "== site -> $WEBROOT"
  sudo mkdir -p "$WEBROOT"
  # --delete so a file removed from the repo is removed from the web root too.
  # Without it a deleted page keeps being served forever.
  sudo rsync -a --delete "$REPO/frontends/" "$WEBROOT/"
  sudo chown -R caddy:caddy "$WEBROOT"
  echo "   synced"
fi

if (( DO_SERVICES )); then
  # services/ is run from the repo directly by systemd, so there is nothing to
  # copy - only a restart. rag_core reloads a 655 MB index and takes ~12 s, and
  # /health returns 503 until its warmup query has run, so this waits rather
  # than reporting success the moment systemctl returns.
  echo "== services"
  sudo systemctl restart shruti-core shruti-gateway
  printf "   waiting for rag_core"
  for _ in $(seq 1 40); do
    if curl -fsS --max-time 3 http://127.0.0.1:8000/health 2>/dev/null | grep -q '"status":"ok"'; then
      echo " ok"; break
    fi
    printf "."; sleep 2
  done
  curl -fsS --max-time 5 http://127.0.0.1:8000/health | sed 's/^/   core    /' || echo "   core    NOT READY"
  curl -fsS --max-time 5 http://127.0.0.1:8001/health | sed 's/^/   gateway /' || echo "   gateway NOT READY"
fi

# VERIFY OVER HTTPS, NOT ON DISK, AND BY HASH RATHER THAN BY GREP.
#
# Checking the file you just copied proves nothing when the whole failure mode is
# that the copy landed somewhere Caddy is not reading - so this fetches what a
# browser fetches. And it compares the WHOLE FILE, because both weaker forms have
# already lied here:
#
#   - grepping for a phrase from the newest change reported "ok" for ui.js while
#     the box served a two-day-old build. The phrase was already in the file.
#   - `curl ... | grep -q` reported STALE for a file that was correct: grep exits
#     on the first match, curl dies of SIGPIPE, and the pipeline's status is
#     curl's. That is the "curl: (23)" you may have seen.
#
# Line endings are normalised because the repo checks out CRLF on the Windows box
# and LF here, so a raw byte comparison would report every file stale forever.
echo "== serving now"
stale=0
for f in index.html docs.html console.js theme.css          _shared/app.js _shared/core.js _shared/ui.js _shared/data.js          _shared/docs.js _shared/base.css; do
  [[ -f "$REPO/frontends/$f" ]] || continue
  want=$(tr -d '' < "$REPO/frontends/$f" | sha256sum | cut -d' ' -f1)
  got=$(curl -fsS --max-time 20 "$HOST/$f?v=$RANDOM" 2>/dev/null | tr -d '' | sha256sum | cut -d' ' -f1)
  if [[ "$want" == "$got" ]]; then
    echo "   ok      $f"
  else
    echo "   STALE   $f"
    stale=$((stale + 1))
  fi
done

echo
if (( stale == 0 )); then
  echo "The live site is this build."
  echo "If the page still looks old in a browser, that is the browser CACHE and not"
  echo "the deploy - Caddy sends no cache-control on these files. Ctrl-F5."
else
  echo "$stale asset(s) did not land. Re-run, or check the rsync output above."
fi
