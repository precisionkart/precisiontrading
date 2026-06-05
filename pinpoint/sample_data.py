"""sample_data.py — deterministic offline fixtures for ``--selftest`` (Section 9).

These mimic the *string-cell* shape of a merged Finviz multi-view pull (so the
to_num parser and gates get exercised) plus a small OHLCV set and a regime read.
No network is ever touched. The fixtures are crafted so that some names pass all
gates and others fail specific ones, and so the earnings list contains both
gap-UP and gap-DOWN names (the gap-down ones must be excluded — reaction >
numbers, Section 7).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Columns mirror the canonical Finviz view headers (Section 4). SMA20/50/200 and
# "52W High" are stored as % DISTANCE strings exactly like Finviz returns them
# (positive = price above the MA; "52W High" negative = below the 52w high).
_UNIVERSE_ROWS: list[dict] = [
    # --- clean leaders that should PASS every gate -------------------------
    dict(Ticker="NVDA", Company="NVIDIA Corp", Sector="Technology",
         Industry="Semiconductors", Price="118.40", **{"Avg Volume": "240.00M"},
         **{"Rel Volume": "2.85"}, Beta="2.10", ATR="4.20", **{"RSI (14)": "68.4"},
         Gap="1.20%", **{"from Open": "0.80%"}, SMA20="4.50%", SMA50="9.20%",
         SMA200="22.40%", **{"52W High": "-2.10%", "52W Low": "180.50%"},
         **{"Perf Week": "3.10%", "Perf Month": "11.20%", "Perf Quart": "28.40%",
            "Perf Half": "41.10%", "Perf Year": "92.30%"},
         **{"EPS this Y": "112.40%", "EPS past 5Y": "65.20%", "Sales past 5Y": "58.30%"},
         Change="1.90%"),
    dict(Ticker="HOOD", Company="Robinhood Markets", Sector="Financial",
         Industry="Capital Markets", Price="62.10", **{"Avg Volume": "31.00M"},
         **{"Rel Volume": "3.40"}, Beta="2.37", ATR="3.10", **{"RSI (14)": "71.0"},
         Gap="2.40%", **{"from Open": "1.10%"}, SMA20="6.80%", SMA50="14.50%",
         SMA200="48.20%", **{"52W High": "-1.20%", "52W Low": "260.00%"},
         **{"Perf Week": "5.40%", "Perf Month": "18.90%", "Perf Quart": "44.10%",
            "Perf Half": "78.40%", "Perf Year": "210.50%"},
         **{"EPS this Y": "140.00%", "EPS past 5Y": "—", "Sales past 5Y": "36.40%"},
         Change="2.80%"),
    dict(Ticker="VRT", Company="Vertiv Holdings", Sector="Industrials",
         Industry="Electrical Equipment", Price="98.70", **{"Avg Volume": "6.20M"},
         **{"Rel Volume": "2.15"}, Beta="1.95", ATR="3.40", **{"RSI (14)": "63.2"},
         Gap="0.40%", **{"from Open": "0.20%"}, SMA20="3.10%", SMA50="7.40%",
         SMA200="19.80%", **{"52W High": "-4.80%", "52W Low": "120.30%"},
         **{"Perf Week": "1.90%", "Perf Month": "8.30%", "Perf Quart": "21.10%",
            "Perf Half": "33.60%", "Perf Year": "74.20%"},
         **{"EPS this Y": "48.20%", "EPS past 5Y": "29.10%", "Sales past 5Y": "27.80%"},
         Change="0.60%"),
    dict(Ticker="CRWV", Company="CoreWeave Inc", Sector="Technology",
         Industry="Software Infrastructure", Price="84.30", **{"Avg Volume": "9.80M"},
         **{"Rel Volume": "4.10"}, Beta="2.60", ATR="6.20", **{"RSI (14)": "74.5"},
         Gap="5.10%", **{"from Open": "2.30%"}, SMA20="9.80%", SMA50="22.40%",
         SMA200="—", **{"52W High": "-0.50%", "52W Low": "95.40%"},
         **{"Perf Week": "9.20%", "Perf Month": "31.40%", "Perf Quart": "68.20%",
            "Perf Half": "—", "Perf Year": "—"},
         **{"EPS this Y": "—", "EPS past 5Y": "—", "Sales past 5Y": "420.00%"},
         Change="4.40%"),
    # --- names that FAIL exactly one gate (to prove gates bite) ------------
    dict(Ticker="LOWV", Company="Low Volume Inc", Sector="Technology",
         Industry="Software", Price="45.00", **{"Avg Volume": "120.00K"},   # < 300k
         **{"Rel Volume": "2.50"}, Beta="1.40", ATR="1.10", **{"RSI (14)": "60.0"},
         Gap="0.10%", **{"from Open": "0.00%"}, SMA20="2.00%", SMA50="5.00%",
         SMA200="12.00%", **{"52W High": "-3.00%", "52W Low": "60.00%"},
         **{"Perf Week": "1.00%", "Perf Month": "5.00%", "Perf Quart": "15.00%",
            "Perf Half": "25.00%", "Perf Year": "40.00%"},
         **{"EPS this Y": "50.00%", "EPS past 5Y": "30.00%", "Sales past 5Y": "30.00%"},
         Change="0.20%"),
    dict(Ticker="FARLO", Company="Far From High Co", Sector="Healthcare",
         Industry="Biotechnology", Price="33.00", **{"Avg Volume": "1.50M"},
         **{"Rel Volume": "2.10"}, Beta="1.80", ATR="1.90", **{"RSI (14)": "45.0"},
         Gap="-0.50%", **{"from Open": "-0.30%"}, SMA20="-2.00%", SMA50="-1.00%",
         SMA200="4.00%", **{"52W High": "-14.00%", "52W Low": "30.00%"},   # >10% below high
         **{"Perf Week": "-1.00%", "Perf Month": "2.00%", "Perf Quart": "8.00%",
            "Perf Half": "12.00%", "Perf Year": "22.00%"},
         **{"EPS this Y": "40.00%", "EPS past 5Y": "28.00%", "Sales past 5Y": "26.00%"},
         Change="-0.40%"),
    dict(Ticker="CHEAP", Company="Cheap Stock Inc", Sector="Consumer",
         Industry="Retail", Price="8.40", **{"Avg Volume": "2.00M"},        # < $10
         **{"Rel Volume": "3.00"}, Beta="2.20", ATR="0.60", **{"RSI (14)": "66.0"},
         Gap="1.00%", **{"from Open": "0.50%"}, SMA20="5.00%", SMA50="11.00%",
         SMA200="20.00%", **{"52W High": "-2.00%", "52W Low": "80.00%"},
         **{"Perf Week": "4.00%", "Perf Month": "12.00%", "Perf Quart": "30.00%",
            "Perf Half": "50.00%", "Perf Year": "85.00%"},
         **{"EPS this Y": "60.00%", "EPS past 5Y": "33.00%", "Sales past 5Y": "31.00%"},
         Change="1.20%"),
    dict(Ticker="DULLV", Company="Dull Volume Co", Sector="Utilities",
         Industry="Utilities", Price="55.00", **{"Avg Volume": "900.00K"},
         **{"Rel Volume": "1.10"}, Beta="0.70", ATR="0.80", **{"RSI (14)": "52.0"},  # RVOL < 2
         Gap="0.00%", **{"from Open": "0.00%"}, SMA20="1.00%", SMA50="2.00%",
         SMA200="6.00%", **{"52W High": "-5.00%", "52W Low": "20.00%"},
         **{"Perf Week": "0.50%", "Perf Month": "1.50%", "Perf Quart": "4.00%",
            "Perf Half": "7.00%", "Perf Year": "11.00%"},
         **{"EPS this Y": "10.00%", "EPS past 5Y": "8.00%", "Sales past 5Y": "5.00%"},
         Change="0.10%"),
    # --- mid-pack leaders that pass gates but score lower ------------------
    dict(Ticker="ANET", Company="Arista Networks", Sector="Technology",
         Industry="Communication Equipment", Price="102.30", **{"Avg Volume": "5.10M"},
         **{"Rel Volume": "2.05"}, Beta="1.60", ATR="3.00", **{"RSI (14)": "59.0"},
         Gap="0.30%", **{"from Open": "0.10%"}, SMA20="2.20%", SMA50="5.80%",
         SMA200="16.40%", **{"52W High": "-6.50%", "52W Low": "70.00%"},
         **{"Perf Week": "1.20%", "Perf Month": "6.10%", "Perf Quart": "14.30%",
            "Perf Half": "22.40%", "Perf Year": "48.10%"},
         **{"EPS this Y": "32.10%", "EPS past 5Y": "31.40%", "Sales past 5Y": "28.90%"},
         Change="0.40%"),
    dict(Ticker="CLS", Company="Celestica Inc", Sector="Technology",
         Industry="Electronic Components", Price="76.50", **{"Avg Volume": "4.40M"},
         **{"Rel Volume": "2.60"}, Beta="2.05", ATR="3.20", **{"RSI (14)": "70.2"},
         Gap="1.80%", **{"from Open": "0.90%"}, SMA20="5.40%", SMA50="12.10%",
         SMA200="38.70%", **{"52W High": "-1.80%", "52W Low": "150.00%"},
         **{"Perf Week": "4.10%", "Perf Month": "15.20%", "Perf Quart": "37.40%",
            "Perf Half": "62.10%", "Perf Year": "165.30%"},
         **{"EPS this Y": "58.40%", "EPS past 5Y": "41.20%", "Sales past 5Y": "22.10%"},
         Change="2.10%"),
]


# Earnings fixture: reported yesterday-after-close / today-before-open shape.
# GAP is the key column; gap-DOWN names MUST be excluded by the pipeline.
_EARNINGS_ROWS: list[dict] = [
    dict(Ticker="HOOD", Company="Robinhood Markets", Sector="Financial",
         Price="62.10", **{"Avg Volume": "31.00M"}, **{"Rel Volume": "3.40"},
         Gap="8.40%",   # gapped UP -> keep
         **{"Perf Quart": "44.10%", "Perf Half": "78.40%", "Perf Year": "210.50%",
            "Perf Month": "18.90%"}),
    dict(Ticker="CLS", Company="Celestica Inc", Sector="Technology",
         Price="76.50", **{"Avg Volume": "4.40M"}, **{"Rel Volume": "3.10"},
         Gap="5.20%",   # gapped UP -> keep
         **{"Perf Quart": "37.40%", "Perf Half": "62.10%", "Perf Year": "165.30%",
            "Perf Month": "15.20%"}),
    dict(Ticker="BADER", Company="Bad Earnings Co", Sector="Technology",
         Price="40.00", **{"Avg Volume": "2.00M"}, **{"Rel Volume": "4.50"},
         Gap="-9.80%",  # gapped DOWN -> EXCLUDE regardless of numbers
         **{"Perf Quart": "55.00%", "Perf Half": "90.00%", "Perf Year": "180.00%",
            "Perf Month": "20.00%"}),
    dict(Ticker="MEHV", Company="Cheap Reaction Inc", Sector="Consumer",
         Price="7.50", **{"Avg Volume": "1.00M"}, **{"Rel Volume": "2.20"},
         Gap="6.00%",   # gapped up but price < $10 -> EXCLUDE on gate
         **{"Perf Quart": "10.00%", "Perf Half": "15.00%", "Perf Year": "25.00%",
            "Perf Month": "5.00%"}),
]


# In finvizfinance 1.3.0 the SCREENER returns these percentage columns as float
# FRACTIONS (0.045 = 4.5%); only valuation EPS/Sales stay '%'-strings. The
# fixtures are authored as readable '%' strings, then these columns are converted
# to fractions so the offline path mirrors live data exactly (and the to_pct
# legacy-string warning stays meaningful).
_FRACTION_COLS = (
    "SMA20", "SMA50", "SMA200", "52W High", "52W Low", "Gap",
    "Perf Week", "Perf Month", "Perf Quart", "Perf Half", "Perf Year", "Change",
)


def _to_fractions(df: pd.DataFrame) -> pd.DataFrame:
    """Convert authored '%'-string columns to float fractions (live shape)."""
    df = df.copy()
    for col in _FRACTION_COLS:
        if col in df.columns:
            df[col] = df[col].map(_pct_string_to_fraction)
    return df


def _pct_string_to_fraction(v):
    if isinstance(v, str):
        s = v.strip()
        if s in ("", "-", "—"):
            return float("nan")
        if s.endswith("%"):
            try:
                return float(s[:-1]) / 100.0
            except ValueError:
                return float("nan")
    return v


def universe_df() -> pd.DataFrame:
    """Merged-Finviz-like universe mirroring finvizfinance 1.3.0 (fractions for
    screener percentage columns, '%'-strings for EPS/Sales)."""
    return _to_fractions(pd.DataFrame(_UNIVERSE_ROWS))


def earnings_df() -> pd.DataFrame:
    """Earnings-reaction fixture (mix of gap-up and gap-down)."""
    return _to_fractions(pd.DataFrame(_EARNINGS_ROWS))


def regime_fixture() -> dict[str, dict]:
    """A bull-regime read for SPY & QQQ (price above 200/50/20, 10>20).

    Values mirror finvizfinance quote fundament: SMA fields as % distance
    strings; positive = price above.
    """
    return {
        "SPY": {"SMA20": "1.80%", "SMA50": "4.20%", "SMA200": "9.60%",
                "Perf Month": "3.10%", "Perf Quarter": "7.40%"},
        "QQQ": {"SMA20": "2.40%", "SMA50": "5.80%", "SMA200": "12.30%",
                "Perf Month": "4.00%", "Perf Quarter": "9.10%"},
    }


def ohlcv_fixture(ticker: str, n: int = 200, shape: str = "high_tight_flag") -> pd.DataFrame:
    """Deterministic synthetic daily OHLCV (no RNG — pure function of ticker).

    Default shape is a High & Tight Flag: a long quiet base, a sharp ~2x surge
    (the flagpole) within the last ~8 weeks, then a tight consolidation near the
    highs. This exercises the full Phase-3 path (pattern -> tight stop -> large
    measured move -> R:R >= 5:1) so the offline Focus list is non-empty.

    `shape="index"` returns a gently rising market proxy (for the beach-ball /
    continuity inputs).
    """
    seed = sum(ord(c) for c in ticker) % 7
    idx = pd.date_range(end="2026-06-05", periods=n, freq="B")
    t = np.arange(n)
    flag_window = 15
    surge_window = 40
    pole_start = n - flag_window - surge_window
    flag_start = n - flag_window

    if shape == "index":
        close = 400.0 + 0.20 * t + 4.0 * np.sin((t + seed) / 20.0)
    else:
        base_level = 30.0 + seed * 0.5
        top_level = base_level * 2.2          # ~+120% surge
        close = np.empty(n, dtype="float64")
        for i in range(n):
            if i < pole_start:                # long quiet base
                close[i] = base_level + 0.6 * np.sin((i + seed) / 6.0)
            elif i < flag_start:              # the flagpole (sharp surge)
                frac = (i - pole_start) / max(surge_window, 1)
                close[i] = base_level + (top_level - base_level) * frac
            else:                             # tight flag near the highs
                k = i - flag_start
                close[i] = top_level - 1.5 - 1.2 * np.sin((k + seed) / 3.0)
    close = np.maximum(close, 5.0)

    # tighten the daily range during the flag (low-volume coil).
    rng = np.where(t >= flag_start, 0.5, 1.0)
    high = close + rng * (0.5 + 0.3 * np.abs(np.cos((t + seed) / 3.0)))
    low = close - rng * (0.5 + 0.3 * np.abs(np.sin((t + seed) / 3.0)))
    open_ = (high + low) / 2.0
    # heavy volume on the pole, drying up in the flag.
    vol = np.where((t >= pole_start) & (t < flag_start), 3_000_000, 1_200_000)
    vol = np.where(t >= flag_start, 700_000, vol).astype("int64")
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=idx,
    )
