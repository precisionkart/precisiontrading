#!/usr/bin/env bash
# Local fallback for the nightly scan (use when GitHub Actions starts getting
# 403'd from its data-center IP). Runs the publish on your Mac's residential IP
# and force-pushes the data branch — same artifact the cloud app reads.
#
# Cron example (weekdays 17:30 ET):
#   30 17 * * 1-5  cd /path/to/pinpoint_scanner && ./scripts/run_scheduled_scan.sh >> /tmp/pinpoint_scan.log 2>&1
# (or use launchd; see DEPLOY.md). Requires the repo to have an `origin` remote.

set -euo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

echo "[$(date)] running scheduled publish..."
PINPOINT_REQUEST_DELAY="${PINPOINT_REQUEST_DELAY:-2}" "$PY" scan.py --publish --cache-top "${CACHE_TOP:-250}"

if git remote get-url origin >/dev/null 2>&1; then
  echo "[$(date)] pushing data branch..."
  git checkout -B data
  git add -f data/latest_scan.json \
             data/store/universe_latest.parquet data/store/universe_latest.json \
             data/ohlcv data/theme_history.json data/ipo_highs.json
  git commit -m "local scheduled scan $(date -u +%Y-%m-%dT%H:%MZ)" || echo "no changes"
  git push -f origin data
  git checkout -
  echo "[$(date)] done."
else
  echo "no 'origin' remote — latest_scan.json written locally only."
fi
