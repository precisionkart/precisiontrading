# Deploy — read-only cloud dashboard (Phase 8, Option A)

Pinpoint deploys as a **read-only** Streamlit Cloud app that never calls Finviz.
A scheduled job (GitHub Actions, or your Mac as a fallback) runs the scan on a
home-ish IP after the close, commits the result to a `data` branch, and the
cloud app renders from that committed snapshot. This sidesteps the Finviz
403/data-center-IP problem entirely, costs nothing, and needs **no secrets**.

```
                          nightly, after close
  GitHub Actions runner ───────────────────────┐   (or your Mac, as fallback)
   python scan.py --publish                     │
        │ writes                                ▼
        ├─ data/latest_scan.json     force-push to  ───►  data branch
        ├─ data/store/universe_latest.parquet         (code from main + today's data)
        └─ data/ohlcv/*.parquet (top ~250)                     │
                                                               ▼  Streamlit Cloud
                                              deploys the `data` branch, PINPOINT_CLOUD=1
                                                               │
                                          reads the local JSON + parquet (zero Finviz calls)
                                                               ▼
                                   Morning Dashboard · My Picks · Full Scan (read-only)
```

## What the scheduled job writes

`python scan.py --publish --cache-top 250` produces, under `data/`:
- `latest_scan.json` — regime, theme rankings, Focus / Targets / Earnings / IPO,
  and an ET timestamp.
- `store/universe_latest.parquet` — the broad normalized universe (RS reference
  **and** the offline fundament source for My Picks).
- `ohlcv/*.parquet` — daily bars for the scan names + the top-250 broad names, so
  the cloud app can draw charts and run pattern/entry logic with no live fetch.

## Step-by-step

1. **Push the repo to GitHub** (main = code only; `data/` is gitignored):
   ```bash
   git init && git add -A && git commit -m "Pinpoint scanner"
   gh repo create pinpoint_scanner --private --source=. --push   # or via the web UI
   ```
2. **Seed the data branch once** (so Streamlit Cloud has something to deploy):
   ```bash
   python scan.py --publish --cache-top 250
   git checkout -B data
   git add -f data/latest_scan.json data/store/universe_latest.* data/ohlcv \
              data/theme_history.json data/ipo_highs.json
   git commit -m "initial scan" && git push -u origin data && git checkout main
   ```
   (Or just trigger the Action once — see step 4 — and let it create the branch.)
3. **Deploy on Streamlit Community Cloud** (free, GitHub-connected):
   - Go to <https://share.streamlit.io> → New app.
   - Repository: your repo. **Branch: `data`.** Main file path: `app/Home.py`.
   - Advanced settings → **Environment variables**: set `PINPOINT_CLOUD = 1`.
     (This flips the app to read-only: it loads `data/latest_scan.json`, disables
     "Run Scan", and grades My Picks from the committed snapshot.)
   - Deploy. The app comes up at `https://<your-app>.streamlit.app`.
4. **Enable the schedule.** The workflow in
   [`.github/workflows/scheduled-scan.yml`](.github/workflows/scheduled-scan.yml)
   runs weekdays at 21:30 UTC (17:30 ET in EDT) and on manual dispatch. Open the
   Actions tab → "scheduled-scan" → "Run workflow" to test it end-to-end. Each run
   force-refreshes the `data` branch; Streamlit Cloud auto-reboots and serves the
   fresh snapshot.

No secrets are required — the cloud app makes zero Finviz calls, and the Action
only needs the default `GITHUB_TOKEN` (granted `contents: write` in the workflow).

## Local fallback (when Actions starts getting 403'd)

GitHub's runners share IP ranges that Finviz may eventually throttle. If the
scheduled scan starts returning empty/partial, run it from your Mac's residential
IP instead with [`scripts/run_scheduled_scan.sh`](scripts/run_scheduled_scan.sh):

```bash
# cron (weekdays 17:30 ET):
30 17 * * 1-5  cd /path/to/pinpoint_scanner && ./scripts/run_scheduled_scan.sh >> /tmp/pinpoint_scan.log 2>&1
```

Or with launchd: create `~/Library/LaunchAgents/com.pinpoint.scan.plist` with a
`StartCalendarInterval` of Hour 17 / Minute 30 invoking the script, then
`launchctl load` it. The script publishes and force-pushes the `data` branch
exactly like the Action.

## 403 troubleshooting

The scan degrades gracefully and prints `⚠️ Partial: Finviz throttled …` rather
than crashing. If the published `latest_scan.json` comes back empty or partial:
1. **Raise the request delay.** Set `PINPOINT_REQUEST_DELAY=3` (env) — the Action
   already uses 2s; bump it.
2. **Switch to the local fallback** above (residential IP) — this resolves the
   large majority of CI 403s.
3. **Finviz Elite (last resort).** If both still throttle, a Finviz Elite session
   helps; see "Switching to Option B" below. (Not implemented in v1 — we are
   deliberately secret-free.)

## Switching to Option B later (live cloud scans)

If you ever want a live "Run Scan" button on the cloud app, you'd add either a
residential **proxy** or a **Finviz Elite session cookie**, supplied via Streamlit
secrets (`st.secrets`) and wired into `FinvizClient.configure(proxies=…)` /
`finvizfinance.util.set_proxy`. The client already has the hooks
(`configure(proxies=…)`, env `PINPOINT_REQUEST_DELAY`). We keep this door open but
do **not** implement it for v1 — Option A is cheaper, secret-free, and reliable
for personal use.
