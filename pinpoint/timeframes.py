"""timeframes.py — time-frame continuity (spec Section 3.8).

Top-down: weekly (dominant trend, Stage-1 bases) -> daily (entries, stops) ->
intraday. When weekly + daily + 65-minute all point to the SAME entry, that is
full continuity — the strongest technical edge.

65-MINUTE LIMITATION (Section 10): free sources (yfinance/Stooq) do not provide
clean 65-minute bars (the 6.5h day = six even 65-min bars; a "1-hour" first bar
is only 30 min and distorts the data). So continuity is APPROXIMATED from weekly
+ daily alignment, and this approximation is surfaced in the score's note and in
docs rather than silently presented as full three-frame continuity.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .ohlcv import add_moving_averages, to_weekly


@dataclass
class ContinuityRead:
    score: float            # 0..1 (1 = weekly+daily fully aligned up)
    weekly_up: bool
    daily_up: bool
    aligned: bool           # both frames agree on up
    note: str = ""


def _trend_up(df: pd.DataFrame) -> bool:
    """A frame is 'up' if close is above the 20 EMA and the short EMAs are
    stacked 5 >= 10 >= 20 (momentum intact)."""
    if df is None or len(df) == 0:
        return False
    last = df.iloc[-1]
    cols = ("EMA5", "EMA10", "EMA20")
    if not all(c in df.columns and last[c] == last[c] for c in cols):
        # fall back to close vs 20 EMA only
        if "EMA20" in df.columns and last.get("EMA20") == last.get("EMA20"):
            return bool(last["Close"] >= last["EMA20"])
        return False
    stacked = last["EMA5"] >= last["EMA10"] >= last["EMA20"]
    return bool(last["Close"] >= last["EMA20"] and stacked)


def continuity(daily: pd.DataFrame) -> ContinuityRead:
    """Compute weekly+daily continuity from a daily OHLCV frame (MAs added as
    needed). Weekly is resampled from the daily series."""
    if daily is None or len(daily) < 30:
        return ContinuityRead(score=0.0, weekly_up=False, daily_up=False,
                              aligned=False, note="insufficient history")

    d = add_moving_averages(daily)
    w = add_moving_averages(to_weekly(daily))

    daily_up = _trend_up(d)
    weekly_up = _trend_up(w)
    aligned = daily_up and weekly_up

    # score: weekly is the dominant frame (0.6), daily the trigger frame (0.4).
    score = (0.6 if weekly_up else 0.0) + (0.4 if daily_up else 0.0)
    note = ("weekly+daily aligned up (65-min approximated)" if aligned
            else "frames not fully aligned")
    return ContinuityRead(score=round(score, 2), weekly_up=weekly_up,
                          daily_up=daily_up, aligned=aligned, note=note)
