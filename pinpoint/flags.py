"""flags.py — plain-English flags[] and warnings[] for a graded name (Phase 10
step 5).

These ADD to (never replace) the existing 10-criterion pill checklist: a quick,
human-readable read of what's good (green flags) and what's concerning (amber
warnings) about a setup, surfaced below the pill grid on the expanded card.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _last(d: pd.DataFrame, col: str) -> float:
    return float(d.iloc[-1][col]) if col in d.columns and len(d) else float("nan")


def _vol_dryup_70(d: pd.DataFrame) -> bool:
    """Last 5 bars' volume < 70% of the prior 20-bar average."""
    if "Volume" not in d.columns or len(d) < 25:
        return False
    recent = d["Volume"].iloc[-5:].mean()
    prior = d["Volume"].iloc[-25:-5].mean()
    return bool(prior and prior == prior and recent < 0.70 * prior)


def _first_pullback_to_20(d: pd.DataFrame) -> bool:
    """Close hugging a rising 20 EMA after a multi-week run (generic version of
    the earnings-flag ema_zone=='20' reaction)."""
    if not {"EMA20", "Close"} <= set(d.columns) or len(d) < 25:
        return False
    last = d.iloc[-1]
    e20, close = last["EMA20"], last["Close"]
    if e20 != e20 or e20 <= 0:
        return False
    near = abs(close - e20) / e20 <= 0.015 and close >= e20      # within 1.5%, above
    rising = e20 > float(d["EMA20"].iloc[-15])                   # 20 EMA trending up
    return bool(near and rising)


# Book-explicit EXIT signals (Phase 11, audit-driven; PDF-211-221). Deterministic
# numeric rules — surfaced as warnings/badges, never auto-actions.
EXIT_SIGNAL_LABELS = {
    "ema10_close_break": "🔔 EXIT SIGNAL — closed below 10 EMA (book trail rule)",
    "climax_trim_5ema": "🔥 CLIMAX — 20%+ above 5 EMA (book trim signal)",
}


def exit_signals(d: pd.DataFrame | None) -> list[str]:
    """Book exit/management signals on the daily OHLCV (with EMA columns):
      - 'ema10_close_break': today's close < 10 EMA AND prior close >= 10 EMA
        (the book's trailing-stop trigger, PDF-219).
      - 'climax_trim_5ema': close >= 1.20 * 5 EMA — extended 20%+ above the 5 EMA
        after a run (the book's aggressive-trim cue, PDF-220-221).
    Returns the list of fired signal KEYS; skips silently if OHLCV/EMAs missing."""
    out: list[str] = []
    if d is None or len(d) < 2:
        return out
    last = d.iloc[-1]
    prev = d.iloc[-2]
    if {"EMA10", "Close"} <= set(d.columns):
        e10, c10p = last["EMA10"], prev["EMA10"]
        if (e10 == e10 and c10p == c10p and last["Close"] < e10 and prev["Close"] >= c10p):
            out.append("ema10_close_break")
    if "EMA5" in d.columns:
        e5 = last["EMA5"]
        if e5 == e5 and e5 > 0 and (last["Close"] - e5) / e5 >= 0.20:
            out.append("climax_trim_5ema")
    return out


def compute_flags(d: pd.DataFrame | None, *, compression_score: float = 0.0,
                  slingshot: bool = False, ef_active: bool = False,
                  ema_zone: str | None = None, eps_this_y: float | None = None,
                  sales_growth: float | None = None,
                  pct_below_high: float | None = None,
                  stage_label: str | None = None) -> tuple[list[str], list[str]]:
    """Return (flags, warnings) — plain-English green/amber tags. `d` is the
    daily OHLCV WITH moving-average columns (add_moving_averages)."""
    flags: list[str] = []
    warnings: list[str] = []
    have = d is not None and len(d) > 0

    # --- positive flags ---
    if ema_zone == "20" or (have and _first_pullback_to_20(d)):
        flags.append("🎯 First Pullback to 20 EMA")
    if slingshot:
        flags.append("🎯 Slingshot")
    if have and _vol_dryup_70(d):
        flags.append("Volume Dry-Up")
    if compression_score is not None and compression_score >= 22:
        flags.append("Tight Coil")
    if ((eps_this_y is not None and eps_this_y == eps_this_y and eps_this_y >= 100) or
            (sales_growth is not None and sales_growth == sales_growth and sales_growth >= 100)):
        flags.append("Triple-Digit Growth")
    if ef_active:
        flags.append("Fresh Earnings Gap")

    # --- negative warnings ---
    if have and {"EMA5", "EMA10", "EMA20"} <= set(d.columns):
        last = d.iloc[-1]
        e5, e10, e20, close = last["EMA5"], last["EMA10"], last["EMA20"], last["Close"]
        if not (e5 > e10 > e20):
            warnings.append("MAs not stacked")
        if e5 == e5 and e5 > 0 and close > e5 * 1.08:
            warnings.append("Extended")
        recent = d.iloc[-5:]
        if "EMA20" in recent.columns and (recent["Close"] < recent["EMA20"]).any():
            warnings.append("Deeper pullback")
        if "SMA50" in d.columns and last["SMA50"] == last["SMA50"] and close < last["SMA50"]:
            warnings.append("Below 50 SMA")
    if pct_below_high is not None and pct_below_high == pct_below_high and pct_below_high > 5:
        warnings.append(f"{pct_below_high:.0f}% from highs")
    if compression_score is not None and compression_score <= 7:
        warnings.append("Loose")

    return flags, warnings
