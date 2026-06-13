#!/usr/bin/env bash
# Local fallback for the once-daily MORNING scan (use when GitHub Actions starts
# getting 403'd from its data-center IP). Runs the full scan + publish on your
# Mac's residential IP and force-pushes the data branch — the same artifact the
# read-only cloud app reads. Mirrors .github/workflows/scheduled-scan.yml.
#
# Run ONCE per weekday morning. Do NOT schedule it intraday or on weekends.
#
# cron example (weekdays 9:30 AM ET):
#   30 9 * * 1-5  cd /path/to/pinpoint_scanner && ./scripts/run_morning_scan.sh >> /tmp/pinpoint_morning.log 2>&1
# (or use launchd; see DEPLOY.md). Requires the repo to have an `origin` remote.

set -euo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

echo "[$(date)] running morning scan + publish..."
PINPOINT_REQUEST_DELAY="${PINPOINT_REQUEST_DELAY:-2}" "$PY" scan.py --all --publish --cache-top "${CACHE_TOP:-250}"

if git remote get-url origin >/dev/null 2>&1; then
  echo "[$(date)] pushing data branch..."
  git checkout -B data
  git add -f data/latest_scan.json \
             data/store/universe_latest.parquet data/store/universe_latest.json \
             data/ohlcv data/theme_history.json data/ipo_highs.json \
             data/earnings_watch.json
  git commit -m "morning scan $(date -u +%Y-%m-%dT%H:%MZ)" || echo "no changes"
  git push -f origin data
  git checkout -
  echo "[$(date)] done."
else
  echo "no 'origin' remote — latest_scan.json written locally only."
fi
