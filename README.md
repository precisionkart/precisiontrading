# Pinpoint Trading Scanner

Pinpoint is a daily stock scanner that implements the **Pinpoint Trading**
methodology. Every evening it pulls data from Finviz plus daily/weekly price
history and produces three ranked watchlists — **Targets**, **Focus**, and
**Earnings** (plus an **IPO** watchlist) — with annotated charts, exact entry
triggers, stops, risk/reward, and a transparent "layers of probability" score
that explains *why* each name ranks where it does. It is a screening aid that
does the legwork of a nightly chart review; you make every decision.

---

## ⚠️ Honest caveats — read these first

- **Not financial advice. No auto-execution.** This is a research/screening tool
  only. It never places trades, connects to a brokerage, or moves money. Every
  trading decision is yours.
- **RS Rating is a PROXY, not IBD's.** IBD's RS Rating is proprietary and can't
  be reproduced. We compute a clearly-labeled proxy — the percentile of a
  front-weighted trailing return across the scanned universe — and label it as
  such everywhere it appears. Because it's a percentile, **it is meaningless for
  a handful of tickers**; the analyzer ranks your picks against a reference
  universe.
- **Finviz can return HTTP 403.** Finviz throttles scrapers (data-center IPs,
  rapid requests). The client sleeps ≥1 s between pages, sends a browser-like
  User-Agent, retries with backoff, and **degrades gracefully** (warns and
  continues — never crashes). A residential IP or a Finviz Elite session may be
  needed for reliable pulls, especially from cloud hosts.
- **65-minute intraday is approximated.** Free sources don't provide clean
  65-minute bars, so time-frame continuity (a strategy input) is approximated
  from **weekly + daily** alignment and labeled as an approximation.
- **An empty Focus list is usually correct, not a bug.** Focus only contains
  names at an actionable low-risk entry (valid pattern + R:R ≥ 5:1). Most names
  most days are extended or mid-base and are correctly excluded.

---

## Quick start

```bash
git clone <this-repo> && cd pinpoint_scanner
make setup                  # create .venv (Python 3.10+) and install requirements
make selftest               # offline end-to-end demo on fixtures (NO network)
python scan.py --all        # live: Targets + Focus + Earnings
```

Without `make`:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scan.py --selftest
```

`--selftest` is deterministic and needs no network — it's how you verify a build.

## Scan modes

```
python scan.py --selftest          # offline deterministic pipeline on fixtures
python scan.py --all               # Targets + Focus + Earnings + IPO (live)
python scan.py --targets           # leaders past every gate, ranked
python scan.py --focus             # the daily shortlist (valid pattern + R:R≥5:1)
python scan.py --earnings          # gapped-up earnings reactions only
python scan.py --ipo               # recent IPOs vs their initial high
python scan.py --report            # everything + charts + HTML/CSV/XLSX to output/
python scan.py --analyze AAPL,MSFT # grade your own tickers through the same engine
python scan.py --ignore-rvol       # drop the RVOL gate (weekend/evening prep)
python scan.py --min-growth        # add hard EPS/Sales QoQ ≥25% Finviz filters
python scan.py --limit N           # cap list sizes
```

Every live run ends with a one-line status, e.g.
`✅ Run complete: 2 Focus, 14 Targets, 6 Earnings, 3 IPOs near high.` — or an
honest `⚠️ Partial: Finviz throttled at HH:MM …` if a fetch failed.

## Concepts

**Targets vs Focus vs Earnings.** *Targets* (~100–200) are leaders that pass
every hard gate — price > $10, avg vol ≥ 300k, RVOL > 2, RS proxy > 90, within
0–10% of the 52-week high — ranked by `pinpoint_score`. *Focus* (~10–15) is the
subset that is *also setting up*: a valid chart pattern plus an entry trigger
with R:R ≥ 5:1. *Earnings* lists names that reported yesterday-after-close or
today-before-open and **gapped up** (any gap-down is excluded regardless of the
numbers — the reaction matters more than the headline).

**Layers of probability (the score).** A setup's grade is how many independent
edges stack. Chart/contraction/pattern are *prerequisites* — a bad chart or an
earnings gap-down disqualifies a name outright. The remaining 14 layers (regime,
Stage 2, hot theme, top industry group, time-frame continuity, beach-ball
relative strength, strong growth, volume, R:R, …) add weight, with time-frame
continuity and beach-ball weighted heaviest. Every row carries an itemized
breakdown of exactly which layers fired. All weights live in
[`pinpoint/config.py`](pinpoint/config.py) — the single source of truth.

**The .89 stop rule.** Liquidity clusters at whole/half/quarter dollar levels,
so stops are placed just below the nearest cluster, ending in a "9": whole −0.11
(45.00 → 44.89), half −0.01 (45.50 → 45.49), quarter −0.06 (45.25 → 45.19). The
stop hugs the *immediate pivot* (the tight coil low), not the full pattern low —
that's how a small risk meets a large reward.

**Prior-advance R:R.** Reward is measured to a *prior-advance projection* (the
up-leg that preceded the consolidation, projected from the breakout — a
generalized "flagpole"), bounded to the recent leg. A name only makes Focus if
that target is ≥ 5× the risk. (Base-depth projection structurally caps R:R near
1–2:1 and was rejected as too conservative for the strategy's stated 5:1.)

For the full strategy, see the embedded spec in the project's master build
prompt (or `pinpoint_trading_strategy_spec.md` if present).

## Web app

```bash
make app          # or: streamlit run app/Home.py
```

A local Streamlit app (light-mode, Inter) over the same engine — no logic is
duplicated. Three pages:

- **Morning Dashboard** — regime dot, today's Focus cards (annotated chart +
  score + layer chips + Entry/Stop/Target/R:R), overnight earnings, and your
  saved watchlist re-graded fresh. Renders instantly from today's cached scan;
  Refresh runs a live scan.
- **My Picks** — paste tickers; each is graded through the same engine. A+
  setups get a full card with a green "✓ Pinpoint setup" pill; near-misses an
  amber pill; failures a per-gate pass/fail checklist + plain-English verdict
  (it never hides a failure). RS is ranked against the last full-scan snapshot.
- **Full Scan** — run Targets/Focus/Earnings/IPO on demand, browse sortable
  tables, and download CSV/XLSX/HTML.

## Output

`python scan.py --report` writes to `output/`:
- `report_YYYY-MM-DD.html` — Inter-typeset page: regime indicator, a card per
  Focus name (score + layer chips, annotated daily+weekly chart, Entry/Stop/
  Target/R:R), a collapsible Targets table, the IPO watchlist, and Earnings.
- annotated chart PNGs under `output/charts/`
- `focus/targets/earnings/ipo_YYYY-MM-DD.csv` and a `pinpoint_YYYY-MM-DD.xlsx`
  workbook (three formatted sheets, green gradient on the Score column).

## Architecture

```
pinpoint/
  config.py        single source of truth: every threshold + Finviz screen
  finviz_client.py Finviz access, to_num/to_pct parsing, defensive columns, regime input
  rs_rating.py     RS PROXY (1-99 percentile of a front-weighted trailing return)
  regime.py        SPY/QQQ market-regime filter (bull / neutral / bear)
  fundamentals.py  growth magnitude + acceleration (3.4)
  stage_trend.py   Weinstein stage, MA stack, beach-ball residual (3.5)
  ohlcv.py         yfinance + Stooq-CSV fallback, parquet cache, EMA/SMA, weekly
  patterns.py      geometric pattern detection + Finviz signal cross-check (3.6)
  entries.py       buy-stop, .89 liquidity stop, prior-advance R:R (3.7)
  timeframes.py    weekly+daily continuity (3.8; 65-min approximated)
  themes.py        sector/theme ETF RS + industry-group RS (3.10)
  ipo.py           recent-IPO initial-high tracking (3.11)
  scoring.py       pinpoint_score (weighted layers + transparent breakdown, 3.9)
  pipeline.py      build_targets / enrich_focus / build_earnings / analyze_tickers
  charts.py        annotated candlesticks (product-grade mplfinance styling)
  report.py        Jinja2 HTML report
  output.py        CSV / XLSX / rich terminal
  analyzer.py      My-Picks grader: gate checklist + verdict per ticker (7B/11)
  store.py         watchlist + universe snapshot + scan cache (web app state)
  sample_data.py   deterministic offline fixtures for --selftest
app/
  Home.py          Morning Dashboard (Streamlit)
  pages/2_My_Picks.py, pages/3_Full_Scan.py
  common.py, style.css
```

## Data sources (all free)

- **Finviz** via `finvizfinance==1.3.0` — universe screen, fundamentals,
  earnings dates, pattern signals, performance (RS proxy), SMA gates, industry
  groups. The custom-column screener is a no-op in this version, so we pull the
  dedicated views (Overview/Valuation/Financial/Performance/Technical) and merge
  on Ticker.
- **OHLCV** via `yfinance` (primary) with a **Stooq** fallback, cached under
  `data/ohlcv/` (parquet).

### Finviz data-format note (deviation from the spec's Section 4)

Verified against `finvizfinance==1.3.0`: the screener returns most percentage
columns (`Perf*`, `SMA20/50/200`, `52W High/Low`, `Gap`, `Change`) as float
**fractions** (`0.0708` = 7.08%), while a few legacy columns (`EPS This Y`,
`Perf 3Y/5Y`, `Change from Open`) are still `"%"` strings, and the per-ticker
quote endpoint returns `"%"` strings throughout. `finviz_client.to_pct()`
normalizes all shapes to percent and warns once if a column expected as a
fraction ever arrives as a legacy string. All the Section-4 filter *codes* were
verified accurate; only the cell *format* changed.

## Troubleshooting

- **Finviz 403 / empty lists.** You're being throttled. Raise
  `CONFIG.network.request_delay_s` to 2–3 s, run from a residential IP, or use a
  Finviz Elite session. The run continues and reports the partial result.
- **Focus is empty.** Usually correct — no name is at an actionable
  pattern+R:R≥5:1 right now. Use `--targets` to see the broader leader list, or
  `--ignore-rvol` for off-hours prep when RVOL is naturally low.
- **yfinance flakes / a ticker has no data.** The fetch falls back to Stooq, then
  to a stale cached copy, then returns an empty frame with a warning — it never
  crashes the run. Re-running later usually resolves transient yfinance issues.

## Deviations log

- **Stooq direct CSV instead of pandas-datareader.** `pandas-datareader` (0.10)
  is unmaintained and crashes on Python 3.12 (imports the removed `distutils`),
  so the Stooq fallback talks to Stooq's free CSV endpoint directly — no
  dependency, more robust.
- **65-minute intraday unavailable.** Free sources don't expose clean 65-min
  bars; time-frame continuity is approximated from weekly+daily and labeled.
- **AAPL/GOOGL demo render.** The showcase report grades AAPL/GOOGL as Focus via
  the analyzer (they qualify on R:R but sit outside the RVOL-gated weekend
  universe). Analyzer-generated reports carry a "demo: ticker-list grading" tag
  to distinguish them from a live scan.
- **My-Picks RS reference is the BROAD universe (Phase 7 fix).** RS is a
  percentile, so it's only meaningful against a wide universe. The reference was
  initially the ~28-name RVOL-gated targets universe — too small and skewed
  toward the day's momentum movers, which deflated normal names' RS. It now uses
  `RS_REFERENCE_SCREEN` (price > $10, avg vol ≥ 300k, near-high — **no RVOL
  gate**), a ~hundreds-of-names pull. (Test: `tests/test_rs_reference.py`.)
- **Earnings-flag EMA zone reads "5" on a fresh breakout (Phase 9).** The book
  treats the 10 EMA as the textbook flag-reaction zone, but a *just-broken-out*
  tight flag still hugs the 5 EMA — that's faithful, not a bug. The matured-flag
  10/20 zones surface on names that consolidated longer. `detect_earnings_flag`
  reports the measured zone; tests assert `ema_zone in (5,10,20)` rather than
  pinning one.
- **Industry-RS gate only fires when industry ranks are present (Phase 9).** The
  top-10% industry gate is enforced on live/publish runs (which carry Finviz
  industry data); the read-only cloud path reads already-gated published targets
  and the offline cache path skips the gate rather than dropping every name.
  `--no-industry-gate` bypasses it for diagnostics.
- **RS-universe consistency via injection, not refetch (Phase 9).** `build_targets`
  keeps the RVOL-gated `TARGETS_SCREEN` for *membership* but ranks RS over the
  broad `RS_REFERENCE_SCREEN` (matching My-Picks/cloud) by injecting a
  precomputed broad-RS column; it recomputes locally only if none is supplied.
  Costs ~1 min of performance-view fetch on a live local run.
- **0-100 rescale: 3 layers migrated OUT of the score (Phase 10 step 1).** The
  new base is 7 ShakeBot-mapped modules (raw weights sum to 110, normalized to
  100); `earnings_flag` (+25), `volume_dry_up` (+2) and `slingshot` (+3) are
  post-normalization bonuses capped at 100. To keep "the 7 modules + 3 bonuses =
  100" mental model exact, `regime_bull` moved to the 5-state regime /
  position-sizing, `strong_growth` became the "Triple-Digit Growth" flag (fires
  only at EPS/Sales ≥ 100% — ordinary 25% growth is table stakes the gates
  already cover), and `ipo_edge` is IPO-page tagging only. Each row keeps
  `score_legacy` for one release; the rescale is a monotonic transform so
  rankings are unchanged.
- **5-state regime degrades on the snapshot path (Phase 10 step 7).** BULL vs
  NEUTRAL-BULL (and BEAR vs VERY-BEAR) need the 10>20 EMA cross, which
  `fetch_regime` derives from SPY/QQQ daily OHLCV. The Finviz-fundament snapshot
  path (no OHLCV) can't see the cross, so it resolves to BULL / NEUTRAL-BEAR /
  BEAR on the SMA stack alone; the live path refines to the two extra states.
  Exposure %s are a labeled SUGGESTION, never a prescription.
- **Earnings gap-down list has no EPS estimate (Phase 10 step 8).** The free
  Finviz earnings screens don't expose the consensus estimate, so the AVOID
  panel shows Ticker / Gap% / Price / RS / EPS-this-year% (a proxy) and a ★ for
  in-universe names — not the "Estimate (EPS)" the ShakeBot mock had.
- **Synthetic demo fixtures are env-gated, not deleted (Phase 10).**
  `tools/seed_earnings_flag_demo.py` is a no-op unless `PINPOINT_DEMO_SEED=1`,
  and the Dashboard quarantines known synthetic tickers (FLAGX, …) from any live
  scan unless that flag is set, logging a tripwire if one leaks into production
  data.

## Requirements

Python 3.10+ (developed on 3.12). All dependencies are free and listed in
[`requirements.txt`](requirements.txt). Ask before adding any paid dependency.

## CLI scan → Dashboard

A terminal scan (`python scan.py --all`) now writes the **Dashboard cache**
(`data/store/cache/`) in addition to the `data/snapshots/` audit log, so the web
app picks up the latest run. The run prints a "📁 Files written" summary at the
end. The Dashboard does **not** auto-poll yet (that's a deferred phase), so after
a CLI scan either **restart** `streamlit run app/Home.py` or click the **↻
Refresh** button in the sidebar to load the fresh data.

## Status

Phases 1–10 complete. 1–8: data layer, screening + scoring,
OHLCV/patterns/entries, charts + reports, themes + IPO, tests + hardening, the
local Streamlit web app, and the read-only cloud deploy path. Phase 9: the
earnings-flag connector (gap-up forward-tracking → flag-breakout Focus path,
badge/prose/Earnings Watch page, RS-universe consistency, industry-RS gate).
Phase 10 (ShakeBot-inspired): 0-100 score rescale + tiers, ATR-normalized
compression, slingshot detection, named flags/warnings, 5-state regime +
suggested position sizing, earnings gaps both directions, and the Pre-Market
Briefing page. See [DEPLOY.md](DEPLOY.md) for the scheduled-scan → data-branch →
Streamlit Cloud architecture (secret-free, no live Finviz from the cloud).
