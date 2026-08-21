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

echo "== repo"
git -C "$REPO" rev-parse --abbrev-ref HEAD | sed 's/^/   branch /'
git -C "$REPO" log --oneline -1 | sed 's/^/   head   /'

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

# VERIFY OVER HTTPS, NOT ON DISK. DONT-FORGET.md 12A: checking the file you just
# copied proves nothing, because the whole failure mode is that the copy landed
# somewhere Caddy is not reading. Fetch the asset the browser fetches.
echo "== serving now"
for probe in "_shared/ui.js:external source" "_shared/data.js:C4"; do
  path="${probe%%:*}"; needle="${probe#*:}"
  if curl -fsS --max-time 10 "$HOST/$path" | grep -q -- "$needle"; then
    echo "   ok      $path contains '$needle'"
  else
    echo "   STALE   $path does NOT contain '$needle'"
  fi
done

echo
echo "If a probe says STALE, the sync did not land. If the browser still shows the"
echo "old page while these say ok, it is the browser cache - Caddy sends no"
echo "cache-control on static files, so Chrome will reuse a heuristically cached"
echo "copy. Ctrl-F5, or DevTools with 'Disable cache' ticked."
