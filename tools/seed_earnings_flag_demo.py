"""tools/seed_earnings_flag_demo.py — DEV TOOL for offline test validation of the
earnings_flag layer. Do NOT run against production scan data.

Injects a synthetic name (FLAGX) that gapped up ~3 weeks ago into
earnings_watch.json, caches a synthetic OHLCV that forms a flag-and-breakout
today, grades it through the REAL enrich_focus, and prepends it to today's
Dashboard cache so the earnings-flag badge + prose are visible in the UI.

GATED: this seeder is a no-op unless PINPOINT_DEMO_SEED=1 is set in the
environment, so it can never accidentally pollute a real scan. The Dashboard
also quarantines known synthetic tickers (see common.quarantine_synthetic)
unless that same flag is set.
"""

import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pinpoint import store, earnings_watch as ew, pipeline
from pinpoint import regime as regime_mod
from pinpoint import sample_data
from pinpoint.config import CONFIG


def _flag_ohlcv():
    """Gap-up ~22 calendar days ago + light-volume flag + breakout today."""
    rows = []
    for i in range(40):
        c = 50 + 0.4 * np.sin(i / 3.0)
        rows.append((c, c + 1.25, c - 1.25, c, 1_000_000))
    rows.append((58.0, 61.0, 57.0, 60.0, 4_000_000))          # gap day
    for k in range(15):
        c = 60.3 + 0.15 * np.sin(k / 2.0)
        rows.append((c, 61.0, 60.0, c, 600_000))              # flag holds the gap
    rows.append((61.2, 62.5, 61.0, 62.0, 1_600_000))          # breakout today
    idx = pd.bdate_range(end=date.today(), periods=len(rows))
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=idx)
    return df, idx[40]                                          # df, gap_date


def main():
    if os.environ.get("PINPOINT_DEMO_SEED") != "1":
        print("seed_earnings_flag_demo: no-op (set PINPOINT_DEMO_SEED=1 to run). "
              "This is a dev-only fixture and must never touch production data.")
        return
    df, gap_date = _flag_ohlcv()
    # cache the synthetic OHLCV so the card chart/sparkline render
    os.makedirs(os.path.join(CONFIG.paths.data_dir, "ohlcv"), exist_ok=True)
    df.to_parquet(os.path.join(CONFIG.paths.data_dir, "ohlcv", "FLAGX_1d.parquet"))

    # seed earnings_watch.json: FLAGX (active, ~22d) + a couple of real names
    ew.save_store([
        {"ticker": "FLAGX", "gap_date": gap_date.date().isoformat(), "gap_pct": 9.0,
         "initial_post_gap_high": 61.0, "sector": "Technology", "theme": "Technology #3",
         "status": "active"},
        {"ticker": "AGX", "gap_date": (date.today() - timedelta(days=12)).isoformat(),
         "gap_pct": 7.2, "initial_post_gap_high": 700.0, "sector": "Industrials",
         "theme": "Industrials #6", "status": "active"},
        {"ticker": "GIII", "gap_date": (date.today() - timedelta(days=3)).isoformat(),
         "gap_pct": 11.9, "initial_post_gap_high": 34.5, "sector": "Consumer Cyclical",
         "theme": "", "status": "active"},
    ])

    # grade FLAGX through the REAL enrich_focus (earnings flag should fire)
    reg = regime_mod.from_fundaments(sample_data.regime_fixture())
    universe = pd.DataFrame([{
        "ticker": "FLAGX", "company": "Flag Demo Inc", "sector": "Technology",
        "industry": "Software", "beta": 1.6, "sma20_pct": 3.0, "sma50_pct": 9.0,
        "sma200_pct": 22.0, "rel_volume": 3.0, "eps_this_y": 60.0, "pct_below_high": 1.5}])
    targets = pd.DataFrame([{
        "ticker": "FLAGX", "company": "Flag Demo Inc", "sector": "Technology",
        "price": 62.0, "rs": 96.0, "stage": "Stage 2 (advancing)",
        "growth": "EPS yr 60%", "theme": "Technology #3"}])
    idx = sample_data.ohlcv_fixture("SPY")
    focus_efx = pipeline.enrich_focus(targets, universe, reg,
                                      ohlcv_provider=lambda t: df, index_daily=idx,
                                      earnings_ctx=ew.active_ctx())
    print("FLAGX graded:", focus_efx[["ticker", "earnings_flag_active",
                                      "earnings_flag_ema_zone", "pinpoint_score",
                                      "pattern"]].to_dict("records"))

    # prepend FLAGX to today's focus parquet (preserve regime/themes meta intact)
    cdir = store._cache_dir()
    fpath = os.path.join(cdir, "focus.parquet")
    if os.path.exists(fpath):
        existing = pd.read_parquet(fpath)
        existing = existing[existing["ticker"] != "FLAGX"]      # idempotent re-seed
        combined = pd.concat([focus_efx, existing], ignore_index=True)
        combined.to_parquet(fpath)
        # stamp the cache date to today so the dashboard renders it (demo only)
        import json
        mpath = os.path.join(cdir, "meta.json")
        if os.path.exists(mpath):
            meta = json.load(open(mpath))
            meta["date"] = date.today().isoformat()
            json.dump(meta, open(mpath, "w"), indent=2)
        print("prepended FLAGX to focus cache:", len(combined), "rows")
    else:
        print("no focus cache — run tools/seed_dashboard.py first")

    # add FLAGX to the snapshot so the analyzer can grade it offline (detail view)
    snap, _ = store.load_universe_snapshot()
    if snap is not None:
        row = {c: universe.iloc[0].get(c) if c in universe.columns else np.nan for c in snap.columns}
        row["ticker"] = "FLAGX"
        for c in ("perf_week", "perf_month", "perf_quarter", "perf_half", "perf_year"):
            if c in snap.columns:
                row[c] = 50.0
        snap2 = pd.concat([pd.DataFrame([row]), snap], ignore_index=True)
        store.save_universe_snapshot(snap2)
        print("added FLAGX to snapshot")


if __name__ == "__main__":
    main()
