"""Pinpoint Trading Scanner.

A daily stock scanner that implements the "Pinpoint Trading" methodology
(see the master strategy spec, Section 3). Research/screening aid only:
this package never places trades, connects to a brokerage, or moves money.

Module map (each module cites the strategy section it implements):
    config        — single source of truth for every threshold + Finviz screen
    finviz_client — Finviz access, to_num parsing, defensive columns, regime inputs
    ohlcv         — OHLCV fetch + cache + EMA/SMA (Phase 3)
    regime        — Section 3.2 market regime filter
    rs_rating     — Section 3.9 RS proxy (1-99 percentile)
    fundamentals  — Section 3.4 growth magnitude + acceleration
    stage_trend   — Section 3.5 Stage analysis, MA stack, beach-ball residual
    patterns      — Section 3.6 geometric pattern detection
    entries       — Section 3.7 triggers, .89 stop, R:R
    timeframes    — Section 3.8 time-frame continuity
    themes        — Section 3.10 sector/theme ETF RS ranking
    ipo           — Section 3.11 IPO initial-high tracking
    scoring       — Section 3.9 pinpoint_score (weighted layers)
    pipeline      — build_targets / build_focus / build_earnings
    analyzer      — Section 11 "My Picks" grader
    charts/report/output/store — rendering & persistence
"""

__version__ = "0.1.0"

DISCLAIMER = (
    "Pinpoint Scanner is a research/screening aid only. It does not give "
    "financial advice, never places trades, and never connects to a brokerage. "
    "All trading decisions are the user's own. Proprietary inputs that cannot be "
    "reproduced exactly (e.g. IBD RS Rating) are computed as clearly-labeled "
    "proxies."
)
