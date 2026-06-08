"""patterns.py — geometric chart-pattern detection (spec Section 3.6).

All Pinpoint patterns are forms of contraction -> expansion (base/flag breakout).
We hunt them only in leaders that already passed the gates (3.3-3.5). For every
pattern the ideal is: market trending up, stock shows relative strength, and a
volume surge on breakout much greater than the volume inside the consolidation.

Implemented detectors (each returns a Pattern with a breakout TRIGGER, a SUPPORT
low for the stop, and a measured-move TARGET where the pattern implies one):
  * Flat base / base breakout
  * High & tight flag
  * Earnings/continuation flag
  * Bullish falling wedge / pennant
  * Bullish descending channel (continuation)
  * Inside day (entry-tactic compression)
  * EMA reclaim (shakeout proven)

Each detector is heuristic and conservative; results are cross-checked against
Finviz's native pattern signals (Section 4) when those are supplied, which sets
`finviz_confirmed` and nudges confidence. Geometry is the source of the trigger/
stop/target; Finviz only corroborates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
import pandas as pd

# Map our pattern names to the Finviz native signal labels that corroborate them.
FINVIZ_CONFIRMS: dict[str, tuple[str, ...]] = {
    "flat_base": ("Horizontal S/R", "Channel"),
    "high_tight_flag": ("Channel Down", "Wedge Down", "Horizontal S/R"),
    "flag": ("Channel Down", "Wedge Down"),
    "falling_wedge": ("Wedge Down", "Wedge", "TL Resistance"),
    "descending_channel": ("Channel Down",),
    "inside_day": (),
    "ema_reclaim": ("TL Support",),
}


@dataclass
class Pattern:
    name: str
    label: str
    trigger: float                 # breakout level to clear
    support_low: float             # for the .89 stop
    measured_target: Optional[float]
    confidence: float              # 0..1 heuristic
    notes: str = ""
    finviz_confirmed: bool = False
    bars: int = 0                  # consolidation length (for chart shading)

    def as_dict(self) -> dict:
        return {
            "pattern": self.label, "trigger": round(self.trigger, 2),
            "support_low": round(self.support_low, 2),
            "measured_target": (round(self.measured_target, 2)
                                if self.measured_target is not None else None),
            "confidence": round(self.confidence, 2),
            "finviz_confirmed": self.finviz_confirmed, "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Small helpers.
# ---------------------------------------------------------------------------
def _pct(a: float, b: float) -> float:
    return (a - b) / b * 100.0 if b else float("nan")


def _swing_high(df: pd.DataFrame, lookback: int) -> float:
    return float(df["High"].iloc[-lookback:].max())


def _swing_low(df: pd.DataFrame, lookback: int) -> float:
    return float(df["Low"].iloc[-lookback:].min())


def _in_uptrend(df: pd.DataFrame) -> bool:
    """Above the 50 SMA and the 200 SMA (Stage-2-ish)."""
    last = df.iloc[-1]
    ok = True
    for col in ("SMA50", "SMA200"):
        if col in df.columns and last[col] == last[col]:
            ok = ok and last["Close"] >= last[col]
    return bool(ok)


def _volume_dryup(df: pd.DataFrame, window: int) -> bool:
    """Volume inside the recent window is lighter than the prior window
    (consolidation drying up)."""
    if "Volume" not in df.columns or len(df) < 2 * window:
        return False
    recent = df["Volume"].iloc[-window:].mean()
    prior = df["Volume"].iloc[-2 * window:-window].mean()
    return bool(recent < prior) if prior and prior == prior else False


def atr(df: pd.DataFrame, period: int = 14) -> float:
    """Average True Range over the last `period` bars (Wilder's TR mean). NaN if
    insufficient data."""
    if df is None or len(df) < 2 or not {"High", "Low", "Close"} <= set(df.columns):
        return float("nan")
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(),
                    (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    tail = tr.iloc[-period:]
    return float(tail.mean()) if len(tail) else float("nan")


def atr_compression(df: pd.DataFrame, period: int = 14) -> dict:
    """ATR-normalized EMA compression (Phase 10 step 3). Replaces the binary
    `emas_converged` with a continuous 0-25 score driving the Compression module.

    Returns the ATR(14), the three EMA/price spreads expressed in ATR units, and
    `compression_score` 0-25. Banding (governed by the LOOSEST of the three
    spreads, since one fanned-out MA breaks the coil):
        all spreads < 0.5 ATR  -> 22-25  ("Tight Coil")
        all spreads < 1.0 ATR  -> 15-21  ("Coiled")
        score 8-14             -> neutral (no flag / no warning)
        score 0-7              -> "Loose"
    """
    from . import ohlcv as ohlcv_mod
    out = {"atr_14": float("nan"), "spread_5_10_atr": float("nan"),
           "spread_10_20_atr": float("nan"), "spread_price_20_atr": float("nan"),
           "compression_score": 0.0}
    d = ohlcv_mod.add_moving_averages(df) if df is not None and len(df) else df
    if d is None or len(d) == 0 or not all(c in d.columns for c in ("EMA5", "EMA10", "EMA20")):
        return out
    a = atr(d, period)
    last = d.iloc[-1]
    if not a or a != a or a <= 0 or last["Close"] <= 0:
        return out
    s_5_10 = (last["EMA5"] - last["EMA10"]) / a
    s_10_20 = (last["EMA10"] - last["EMA20"]) / a
    s_px_20 = (last["Close"] - last["EMA20"]) / a
    out.update(atr_14=round(a, 4), spread_5_10_atr=round(s_5_10, 3),
               spread_10_20_atr=round(s_10_20, 3), spread_price_20_atr=round(s_px_20, 3))

    m = max(abs(s_5_10), abs(s_10_20), abs(s_px_20))      # loosest spread governs
    if m < 0.5:
        score = 22.0 + (0.5 - m) / 0.5 * 3.0              # 22..25
    elif m < 1.0:
        score = 15.0 + (1.0 - m) / 0.5 * 6.0              # 15..21
    elif m < 2.0:
        score = 8.0 + (2.0 - m) / 1.0 * 6.0               # 8..14
    else:
        score = 8.0 - (m - 2.0) * 4.0                     # 8..0 then clamp
    out["compression_score"] = round(max(0.0, min(25.0, score)), 1)
    return out


# Continuation patterns project the PRIOR ADVANCE (the up-leg that preceded the
# consolidation) from the breakout — a generalized "flagpole" — per the user's
# measured-move decision. The leg lookback is bounded so it's the recent advance,
# not a multi-year run.
PRIOR_ADVANCE_LOOKBACK = 80


def _prior_advance_target(df: pd.DataFrame, trigger: float, consol_len: int,
                          fallback: Optional[float] = None,
                          leg_lookback: int = PRIOR_ADVANCE_LOOKBACK) -> Optional[float]:
    """measured = trigger + (trigger - prior_leg_low), where prior_leg_low is the
    lowest low of the up-leg INTO the consolidation (the `consol_len` most recent
    bars are the consolidation; the leg is the `leg_lookback` bars before it)."""
    n = len(df)
    end = n - consol_len                     # consolidation starts here
    start = max(0, end - leg_lookback)
    if end <= start:
        return fallback
    leg_low = float(df["Low"].iloc[start:end].min())
    if leg_low <= 0 or leg_low >= trigger:
        return fallback
    return trigger + (trigger - leg_low)


# ---------------------------------------------------------------------------
# Detectors.
# ---------------------------------------------------------------------------
def detect_flat_base(df: pd.DataFrame, windows: Sequence[int] = (15, 20, 25, 30),
                     max_depth_pct: float = 15.0, near_high_pct: float = 10.0) -> Optional[Pattern]:
    """Tight sideways rest near the highs; breakout above the top (3.6).

    Bases form over varying lengths (the spec's ~6 weeks is the long end; tighter
    4-week bases are common), so we try several windows and keep the TIGHTEST
    qualifying base (smallest depth). near_high is aligned to the 0-10%-below-high
    universe gate.
    """
    close = float(df["Close"].iloc[-1])
    best: Optional[Pattern] = None
    for window in windows:
        if len(df) < window + 5:
            continue
        base = df.iloc[-window:]
        base_top = float(base["High"].max())
        base_low = float(base["Low"].min())
        depth = _pct(base_top, base_low)
        if base_top <= 0 or depth > max_depth_pct:
            continue
        if _pct(base_top, close) > near_high_pct:
            continue
        conf = 0.5 + 0.3 * (max_depth_pct - depth) / max_depth_pct
        if _volume_dryup(df, window // 2):
            conf += 0.1
        # prior-advance projection (fallback to base depth if leg unavailable)
        measured = _prior_advance_target(df, base_top, window,
                                         fallback=base_top + (base_top - base_low))
        cand = Pattern("flat_base", "Flat base / base breakout", trigger=base_top,
                       support_low=base_low, measured_target=measured,
                       confidence=min(conf, 0.95), bars=window,
                       notes=f"{window}-bar base, depth {depth:.1f}%")
        if best is None or depth < _pct(best.trigger, best.support_low):
            best = cand
    return best


def detect_high_tight_flag(df: pd.DataFrame, surge_window: int = 40,
                           min_surge_pct: float = 90.0, flag_window: int = 15,
                           max_flag_depth_pct: float = 25.0) -> Optional[Pattern]:
    """+90-100% surge in 1-8 weeks, then a tight 1-3 week consolidation near the
    highs (3.6, ⭐ pattern)."""
    if len(df) < surge_window + flag_window:
        return None
    flag = df.iloc[-flag_window:]
    pre = df.iloc[-(surge_window + flag_window):-flag_window]
    if len(pre) < 5:
        return None
    surge = _pct(float(flag["High"].max()), float(pre["Low"].min()))
    if surge < min_surge_pct:
        return None
    flag_top = float(flag["High"].max())
    flag_low = float(flag["Low"].min())
    depth = _pct(flag_top, flag_low)
    if depth > max_flag_depth_pct:
        return None
    flagpole = float(flag["Low"].iloc[0]) - float(pre["Low"].min())
    measured = _prior_advance_target(df, flag_top, flag_window,
                                     fallback=flag_top + max(flagpole, 0.0))
    conf = 0.7 + 0.2 * (max_flag_depth_pct - depth) / max_flag_depth_pct
    return Pattern("high_tight_flag", "High & tight flag", trigger=flag_top,
                   support_low=flag_low, measured_target=measured,
                   confidence=min(conf, 0.97), bars=flag_window,
                   notes=f"surge {surge:.0f}%, flag depth {depth:.1f}%")


def detect_flag(df: pd.DataFrame, pole_window: int = 12, min_pole_pct: float = 20.0,
                flag_window: int = 10, max_flag_depth_pct: float = 12.0) -> Optional[Pattern]:
    """Earnings/continuation flag: a sharp pole, then a light, shallow,
    sideways/down flag, best reactions off the 10 EMA (3.6)."""
    if len(df) < pole_window + flag_window:
        return None
    flag = df.iloc[-flag_window:]
    pole = df.iloc[-(pole_window + flag_window):-flag_window]
    if len(pole) < 3:
        return None
    pole_gain = _pct(float(pole["High"].max()), float(pole["Low"].min()))
    if pole_gain < min_pole_pct:
        return None
    flag_top = float(flag["High"].max())
    flag_low = float(flag["Low"].min())
    depth = _pct(flag_top, flag_low)
    if depth > max_flag_depth_pct:
        return None
    pole_height = float(pole["High"].max()) - float(pole["Low"].min())
    measured = _prior_advance_target(df, flag_top, flag_window,
                                     fallback=flag_top + pole_height)
    conf = 0.6 + 0.2 * (max_flag_depth_pct - depth) / max_flag_depth_pct
    if _volume_dryup(df, flag_window):
        conf += 0.1
    return Pattern("flag", "Continuation / earnings flag", trigger=flag_top,
                   support_low=flag_low, measured_target=measured,
                   confidence=min(conf, 0.95), bars=flag_window,
                   notes=f"pole {pole_gain:.0f}%, flag depth {depth:.1f}%")


def detect_falling_wedge(df: pd.DataFrame, window: int = 20) -> Optional[Pattern]:
    """In an uptrend: lower highs & lower lows, narrowing range, volume drying
    up; upside break on volume (3.6)."""
    if len(df) < window + 5 or not _in_uptrend(df):
        return None
    seg = df.iloc[-window:]
    highs = seg["High"].to_numpy()
    lows = seg["Low"].to_numpy()
    x = np.arange(window)
    hi_slope = np.polyfit(x, highs, 1)[0]
    lo_slope = np.polyfit(x, lows, 1)[0]
    # wedge: both slopes down, range narrowing (lows fall slower than highs).
    if not (hi_slope < 0 and lo_slope < 0 and lo_slope > hi_slope):
        return None
    start_range = highs[0] - lows[0]
    end_range = highs[-1] - lows[-1]
    if not (end_range < start_range):
        return None
    trigger = float(seg["High"].iloc[-min(5, window):].max())
    support_low = float(seg["Low"].min())
    measured = _prior_advance_target(df, trigger, window, fallback=trigger + start_range)
    conf = 0.55 + (0.15 if _volume_dryup(df, window // 2) else 0.0)
    return Pattern("falling_wedge", "Bullish falling wedge / pennant",
                   trigger=trigger, support_low=support_low, measured_target=measured,
                   confidence=conf, bars=window,
                   notes=f"narrowing range {start_range:.2f}->{end_range:.2f}")


def detect_descending_channel(df: pd.DataFrame, window: int = 20) -> Optional[Pattern]:
    """Controlled low-volume parallel drift lower inside a greater uptrend
    (continuation); break above the channel (3.6)."""
    if len(df) < window + 5 or not _in_uptrend(df):
        return None
    seg = df.iloc[-window:]
    highs = seg["High"].to_numpy()
    lows = seg["Low"].to_numpy()
    x = np.arange(window)
    hi_slope = np.polyfit(x, highs, 1)[0]
    lo_slope = np.polyfit(x, lows, 1)[0]
    # parallel down channel: both slopes down and roughly parallel.
    if not (hi_slope < 0 and lo_slope < 0):
        return None
    if abs(hi_slope - lo_slope) > 0.5 * abs(hi_slope + 1e-9):
        return None                                    # not parallel enough
    trigger = float(seg["High"].iloc[-min(5, window):].max())
    support_low = float(seg["Low"].min())
    channel_height = float(np.median(highs - lows))
    measured = _prior_advance_target(df, trigger, window,
                                     fallback=trigger + 2.0 * channel_height)
    conf = 0.5 + (0.15 if _volume_dryup(df, window // 2) else 0.0)
    return Pattern("descending_channel", "Bullish descending channel",
                   trigger=trigger, support_low=support_low, measured_target=measured,
                   confidence=conf, bars=window,
                   notes="parallel low-volume drift in uptrend")


def detect_earnings_flag(df: pd.DataFrame, gap_date, gap_high: float,
                         touch_atr: float = 0.6) -> dict:
    """Gap-anchored earnings flag (spec 3.6 ⭐ — the highest-edge setup).

    Unlike detect_flag (which infers a pole), this takes the persisted earnings
    gap as the flagpole anchor and checks the consolidation since:
      * 4-28 bars since the gap (the 1-4 week window),
      * light volume (flag mean < 80% of the pre-gap 20-bar mean),
      * tight, holding above the gap-day close (didn't fill the gap),
      * today is a breakout: close > the flag's prior high on RVOL > 1.5,
      * identifies which EMA (10/5/20) the flag's lows hugged (the reaction zone;
        10 EMA = best entry, 5 = short pop, 20 = matured flag).

    Returns a dict (detected, ema_zone, flag_days, flag_compression_pct,
    breakout_volume_ratio).
    """
    from . import ohlcv as ohlcv_mod
    out = {"detected": False, "ema_zone": None, "flag_days": 0,
           "flag_compression_pct": float("nan"), "breakout_volume_ratio": float("nan")}
    if df is None or len(df) < 25:
        return out
    d = ohlcv_mod.add_moving_averages(df)
    try:
        pos = d.index.get_indexer([pd.Timestamp(gap_date)], method="nearest")[0]
    except Exception:  # noqa: BLE001
        return out
    last = len(d) - 1
    flag_days = last - pos
    if flag_days < 4 or flag_days > 28:
        return out

    gap_bar = d.iloc[pos]
    flag = d.iloc[pos + 1:last]            # consolidation, excluding the breakout bar
    today = d.iloc[last]
    if len(flag) < 3:
        return out

    pre = d.iloc[max(0, pos - 20):pos]
    pre_vol = pre["Volume"].mean() if len(pre) else float("nan")
    flag_vol = flag["Volume"].mean()
    light_volume = bool(pre_vol == pre_vol and flag_vol < 0.8 * pre_vol)

    held_gap = bool(flag["Low"].min() >= gap_bar["Close"])     # didn't fill the gap
    flag_high = float(flag["High"].max())
    compression = (flag_high - float(flag["Low"].min())) / today["Close"] * 100.0
    breakout = bool(today["Close"] > flag_high)
    vol_ratio = today["Volume"] / flag_vol if flag_vol else float("nan")
    breakout_vol = bool(vol_ratio == vol_ratio and vol_ratio > 1.5)

    # EMA reaction zone — which EMA did the flag lows hug most (within touch_atr*ATR)?
    ema_zone = None
    atr = (d["High"] - d["Low"]).iloc[max(0, pos - 14):pos].mean()
    if atr and atr == atr:
        touches = {"5": 0, "10": 0, "20": 0}
        for _, bar in flag.iterrows():
            for z, col in (("5", "EMA5"), ("10", "EMA10"), ("20", "EMA20")):
                if col in flag.columns and bar[col] == bar[col]:
                    if abs(bar["Low"] - bar[col]) <= touch_atr * atr:
                        touches[z] += 1
        if max(touches.values()) > 0:
            # prefer 10 EMA on ties (the book's best entry)
            ema_zone = max(("10", "5", "20"), key=lambda z: touches[z])

    out.update({"detected": bool(light_volume and held_gap and breakout and breakout_vol),
                "ema_zone": ema_zone, "flag_days": int(flag_days),
                "flag_compression_pct": round(compression, 2),
                "breakout_volume_ratio": round(vol_ratio, 2) if vol_ratio == vol_ratio else None})
    return out


def detect_inside_day(df: pd.DataFrame) -> Optional[Pattern]:
    """Inside day: lower high AND higher low vs the prior bar (compression).
    Entry through the inside-day high; stop below the inside-day low (or the
    prior day's low if a few cents lower) (3.7)."""
    if len(df) < 2:
        return None
    last, prev = df.iloc[-1], df.iloc[-2]
    if not (last["High"] < prev["High"] and last["Low"] > prev["Low"]):
        return None
    support_low = min(float(last["Low"]), float(prev["Low"]))
    return Pattern("inside_day", "Inside day (compression)",
                   trigger=float(last["High"]), support_low=support_low,
                   measured_target=None, bars=2,
                   confidence=0.45, notes="meaningful at a MA / S-R level")


def detect_ema_reclaim(df: pd.DataFrame, ema_col: str = "EMA20",
                       lookback: int = 8) -> Optional[Pattern]:
    """A trending stock loses then reclaims the 10/20 EMA -> shakeout proven;
    buy the reclaim (3.7)."""
    if ema_col not in df.columns or len(df) < lookback + 2:
        return None
    seg = df.iloc[-lookback:]
    below = (seg["Close"] < seg[ema_col]).any()
    last = df.iloc[-1]
    reclaimed = last["Close"] > last[ema_col]
    if not (below and reclaimed):
        return None
    support_low = float(seg["Low"].min())
    trigger = float(seg["High"].max())
    return Pattern("ema_reclaim", f"{ema_col} reclaim (shakeout proven)",
                   trigger=trigger, support_low=support_low, measured_target=None,
                   confidence=0.5, bars=lookback, notes=f"lost then reclaimed {ema_col}")


# ---------------------------------------------------------------------------
# Aggregator.
# ---------------------------------------------------------------------------
_DETECTORS = (
    detect_high_tight_flag,
    detect_flat_base,
    detect_flag,
    detect_falling_wedge,
    detect_descending_channel,
    detect_ema_reclaim,
    detect_inside_day,
)


def detect_patterns(df: pd.DataFrame,
                    finviz_signals: Optional[Sequence[str]] = None) -> list[Pattern]:
    """Run all detectors; cross-check against Finviz native signals; return
    patterns sorted by confidence (Finviz-confirmed first)."""
    if df is None or len(df) < 5 or "Close" not in df.columns:
        return []
    signals = set(finviz_signals or ())
    found: list[Pattern] = []
    for det in _DETECTORS:
        try:
            pat = det(df)
        except Exception:  # noqa: BLE001 — one detector must never break the scan
            pat = None
        if pat is None:
            continue
        confirms = FINVIZ_CONFIRMS.get(pat.name, ())
        if signals and any(s in signals for s in confirms):
            pat.finviz_confirmed = True
            pat.confidence = min(pat.confidence + 0.1, 0.99)
        found.append(pat)

    found.sort(key=lambda p: (p.finviz_confirmed, p.confidence), reverse=True)
    return found


def best_pattern(df: pd.DataFrame,
                 finviz_signals: Optional[Sequence[str]] = None,
                 require_measured: bool = False) -> Optional[Pattern]:
    """The single highest-confidence pattern. If require_measured, only patterns
    that imply a measured target are eligible (needed for the R:R gate)."""
    pats = detect_patterns(df, finviz_signals)
    if require_measured:
        pats = [p for p in pats if p.measured_target is not None]
    return pats[0] if pats else None
