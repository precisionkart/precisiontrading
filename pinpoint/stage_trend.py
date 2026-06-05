"""stage_trend.py — Stage analysis, MA alignment, beach-ball (spec Section 3.5).

We trade only **Stage 2** (advancing). Stages:
  1 basing     — sideways after a downtrend, 30-week MA flattens (watchlist it)
  2 advancing  — breaks base on volume, above a rising 30-week/200d MA, RS+  (BUY)
  3 topping    — sideways, MA flattens (tighten/reduce)
  4 declining  — price below MA, MA turns down (avoid)

Snapshot path (Phase 2): classify from Finviz SMA20/50/200 % distances. The
200-SMA *slope* and the precise 30-week MA aren't in the snapshot, so "rising"
is approximated by price being above the 200 with the 50 above the 200 — a
labeled proxy. OHLCV refines this in Phase 3.

Beach Ball Under Water (Section 3.5, highest-value RS signal): while the market
sells off, the stock goes sideways/up. Quantified as the residual
``stock_return - beta * index_return`` over a market-down window; a large
positive residual (and the stock holding its 200 SMA while the index loses its
own) marks a strong beach ball. The residual math lives here; it is wired to
OHLCV in Phase 3.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

STAGE_LABELS = {1: "Stage 1 (basing)", 2: "Stage 2 (advancing)",
                3: "Stage 3 (topping)", 4: "Stage 4 (declining)", 0: "unclassified"}


@dataclass
class StageRead:
    stage: int
    label: str
    ma_stack_ok: bool        # close > 50 > 200 (rising) proxy
    is_stage2: bool
    note: str = ""


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def classify_from_sma(sma20_pct: float, sma50_pct: float, sma200_pct: float) -> StageRead:
    """Classify stage from price-to-MA % distances (positive = price above MA).

    50>200 is inferred from the distances: if price is *further* above the 200
    than above the 50, the 50 sits above the 200 (proxy).
    """
    s20, s50, s200 = _f(sma20_pct), _f(sma50_pct), _f(sma200_pct)

    above200 = s200 > 0 if s200 == s200 else None
    above50 = s50 > 0 if s50 == s50 else None
    # 50 above 200 proxy: distance-to-200 exceeds distance-to-50 (both vs same price)
    fifty_above_200 = (s200 > s50) if (s200 == s200 and s50 == s50) else None

    if above200 is False:
        stage, ok = 4, False
        note = "price below 200 SMA"
    elif above200 and above50 and fifty_above_200:
        stage, ok = 2, True
        note = "close > 50 > 200 (Stage 2 proxy)"
    elif above200 and (above50 is False):
        stage, ok = 3, False
        note = "above 200 but lost the 50 SMA (topping proxy)"
    elif above200:
        stage, ok = 1, False
        note = "above 200, MAs not yet stacked (basing/early proxy)"
    else:
        stage, ok = 0, False
        note = "insufficient MA data"

    return StageRead(stage=stage, label=STAGE_LABELS[stage], ma_stack_ok=ok,
                     is_stage2=(stage == 2), note=note)


def assess_row(row: pd.Series) -> StageRead:
    """Stage read from a normalized universe row (snapshot path)."""
    return classify_from_sma(row.get("sma20_pct"), row.get("sma50_pct"),
                             row.get("sma200_pct"))


# ---------------------------------------------------------------------------
# Beach Ball residual (math now; OHLCV wiring in Phase 3).
# ---------------------------------------------------------------------------
@dataclass
class BeachBall:
    residual: float          # stock_return - beta * index_return over the window
    stock_return: float
    index_return: float
    beta: float
    is_beach_ball: bool
    note: str = ""


def beach_ball_residual(stock_close: pd.Series, index_close: pd.Series,
                        beta: float, window: int = 20,
                        min_index_drop: float = -3.0,
                        min_residual: float = 5.0) -> BeachBall:
    """Residual relative strength over the last `window` bars of a market-down
    period (Section 3.5).

    A positive residual means the stock outperformed what its beta predicted —
    holding up (or rising) while the index fell. The flag requires the index to
    actually be down over the window (a "beach ball under water" only counts
    during market weakness) and the residual to clear `min_residual` (%).
    """
    if stock_close is None or index_close is None or len(stock_close) < window + 1 \
            or len(index_close) < window + 1:
        return BeachBall(np.nan, np.nan, np.nan, beta, False, "insufficient history")

    s = stock_close.iloc[-(window + 1):]
    i = index_close.iloc[-(window + 1):]
    stock_ret = (s.iloc[-1] / s.iloc[0] - 1.0) * 100.0
    index_ret = (i.iloc[-1] / i.iloc[0] - 1.0) * 100.0
    b = beta if beta == beta else 1.0
    residual = stock_ret - b * index_ret

    is_bb = bool(index_ret <= min_index_drop and residual >= min_residual)
    note = ("strong beach ball: held up while market fell" if is_bb
            else "no beach-ball signal in this window")
    return BeachBall(residual=residual, stock_return=stock_ret, index_return=index_ret,
                     beta=b, is_beach_ball=is_bb, note=note)
