"""tools/shot_card.py — DEV TOOL: render one expanded detail card for a
screenshot (Phase 10). Builds a PickResult from FLAGX's actual graded focus row
(real engine output: ATR readout, flags/warnings, chart with right-edge pills)
and renders render_detail_inline. Run: streamlit run tools/shot_card.py
"""

import os
import sys

import streamlit as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, ROOT)

import common as c
from pinpoint import store, analyzer
from pinpoint import ohlcv as ohlcv_mod

c.page_config("Pinpoint card")
c.inject_css()

cache = store.load_scan_cache()
focus = cache.lists["focus"]
row = focus[focus["ticker"] == "FLAGX"].iloc[0]
daily = ohlcv_mod.fetch_daily("FLAGX", cache_only=True).df

pr = analyzer.PickResult(
    ticker="FLAGX", sector=row.get("sector"), theme=row.get("theme"),
    classification="A+", pattern=row.get("pattern"),
    pattern_bars=int(row.get("pattern_bars") or 0), score=row.get("pinpoint_score"),
    tier=row.get("tier"), rs=row.get("rs"), entry=row.get("entry_trigger"),
    stop=row.get("stop"), target=row.get("measured_target"),
    reward_risk=row.get("reward_risk"), layers=row.get("layers", ""),
    daily=ohlcv_mod.add_moving_averages(daily),
    atr_14=row.get("atr_14"), compression_score=row.get("compression_score"),
    spread_5_10_atr=row.get("spread_5_10_atr"),
    spread_10_20_atr=row.get("spread_10_20_atr"),
    spread_price_20_atr=row.get("spread_price_20_atr"),
    flags=list(row.get("flags") or []), warnings=list(row.get("warnings") or []),
    earnings_flag_active=True, earnings_flag_ema_zone=row.get("earnings_flag_ema_zone"),
    gap_pct=row.get("gap_pct"), gap_date=row.get("gap_date"))

st.markdown("<div class='pp-h1'>FLAGX <span class='dim'>expanded card</span></div>",
            unsafe_allow_html=True)
c.render_detail_inline(pr)
