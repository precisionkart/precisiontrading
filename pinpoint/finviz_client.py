"""finviz_client.py — numeric/string parsing utilities (Finviz data source REMOVED).

The live Finviz data source has been replaced by Massive (see massive_client.py).
What remains here are the SOURCE-AGNOSTIC parsing helpers the pipeline still uses
to normalise string cells into numbers/percentages:
  * to_num / to_pct — robust numeric & percentage parsing ('12.3%', '1.5M', '-').
  * pick_column / get_col — defensive column access by candidate names.
  * COLUMN_CANDIDATES — canonical header candidate lists.
  * FinvizResult — legacy result container (retained for back-compat).

The networked FinvizClient class and all finvizfinance access were deleted in the
Massive migration; nothing in the package imports finvizfinance any more.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

import numpy as np
import pandas as pd

from .config import CONFIG, FINVIZ_VIEWS

logger = logging.getLogger("pinpoint.finviz")


# ---------------------------------------------------------------------------
# Numeric parsing (Section 4: cells look like '12.34%', '1.5M', '-').
# ---------------------------------------------------------------------------
_MULTIPLIERS = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def to_num(value: Any) -> float:
    """Parse a Finviz cell into a float; '-'/empty/unparseable -> NaN.

    Strips '%' and '$', applies K/M/B/T multipliers, handles parentheses as
    negatives and embedded commas. Idempotent for numbers already numeric.
    """
    if value is None:
        return float("nan")
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip()
    # Normalise the Unicode minus sign to ASCII so "−3.5%" parses as negative.
    s = s.replace("−", "-")
    # Any cell that is only dash variants / whitespace / placeholders -> NaN.
    _DASHES = "-‐‑‒–—―"   # hyphen + figure/en/em/etc.
    if s == "" or all(ch in _DASHES for ch in s) or s in ("N/A", "NA", "nan", "None"):
        return float("nan")

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]

    s = s.replace("$", "").replace(",", "").replace("%", "")
    s = "".join(s.split())          # drop ALL internal whitespace (incl. NBSP)
    if s.startswith("-"):
        negative = True
        s = s[1:]

    mult = 1.0
    if s and s[-1].upper() in _MULTIPLIERS:
        mult = _MULTIPLIERS[s[-1].upper()]
        s = s[:-1]

    try:
        num = float(s) * mult
    except ValueError:
        return float("nan")
    return -num if negative else num


def to_num_series(series: pd.Series) -> pd.Series:
    """Vectorised to_num over a pandas Series."""
    return series.map(to_num).astype("float64")


_warned_legacy_pct_string = False


def to_pct(value: Any, expect_fraction: bool = False) -> float:
    """Parse a *percentage-semantic* Finviz cell into PERCENT units.

    IMPORTANT (verified against finvizfinance==1.3.0): the screener now returns
    most percentage columns (Perf*, SMA20/50/200, 52W High/Low, Gap, Change,
    Volatility) as float **fractions** (e.g. 0.0708 = 7.08%), while a few legacy
    columns (valuation 'EPS This Y', 'Perf 3Y/5Y/10Y', 'Change from Open') remain
    '%'-suffixed strings (e.g. '15.09%'). The spec's Section-4 assumption that
    every cell is a '12.34%' string is OUTDATED for this version. This function
    normalizes BOTH shapes to percent:
        - '15.09%'  -> 15.09     (string already in percent)
        - 0.0708    -> 7.08      (float fraction scaled to percent)
    Centralised here so a future package change is a one-line fix.

    `expect_fraction=True` marks columns that SHOULD arrive as float fractions in
    this package version; if such a column is ever seen as a legacy '%' string,
    we log a one-time warning so we get a heads-up that Finviz/finvizfinance may
    have flipped its format back (which would otherwise risk a 100x scale error).
    """
    global _warned_legacy_pct_string
    if value is None:
        return float("nan")
    if isinstance(value, str):
        s = value.strip()
        if s.endswith("%"):
            if expect_fraction and not _warned_legacy_pct_string:
                logger.warning(
                    "to_pct saw a legacy '%%' string for a column expected as a "
                    "float fraction — Finviz format may have changed; verify "
                    "to_pct scaling in finviz_client.py.")
                _warned_legacy_pct_string = True
            return to_num(s)              # already percent
        x = to_num(s)                     # bare numeric string -> treat as fraction
        return x * 100.0 if x == x else x
    if isinstance(value, (int, float)):
        x = float(value)
        return x * 100.0 if x == x else x
    return to_num(value)


def to_pct_series(series: pd.Series, expect_fraction: bool = False) -> pd.Series:
    """Vectorised to_pct over a pandas Series."""
    return series.map(lambda v: to_pct(v, expect_fraction=expect_fraction)).astype("float64")


# ---------------------------------------------------------------------------
# Defensive column access (scraped header names drift between views/versions).
# ---------------------------------------------------------------------------
def pick_column(df: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
    """Return the first existing column matching any candidate (case-insensitive,
    ignoring surrounding whitespace). Returns None if none match."""
    norm = {c.strip().lower(): c for c in df.columns}
    for cand in candidates:
        key = cand.strip().lower()
        if key in norm:
            return norm[key]
    # loose contains-match fallback
    for cand in candidates:
        key = cand.strip().lower()
        for col_norm, col in norm.items():
            if key == col_norm or key in col_norm:
                return col
    return None


def get_col(df: pd.DataFrame, candidates: Sequence[str], numeric: bool = False,
            pct: bool = False, expect_fraction: bool = False) -> pd.Series:
    """Fetch a column by candidate names; returns an all-NaN/empty Series if
    absent so downstream code never KeyErrors (Section 9 graceful degradation).

    numeric=True parses raw numbers (price/volume/beta/...); pct=True parses
    percentage-semantic columns to PERCENT units via to_pct (handles both the
    float-fraction and '%'-string shapes this package version mixes);
    expect_fraction=True additionally arms the legacy-format warning (see
    to_pct)."""
    col = pick_column(df, candidates)
    if col is None:
        logger.debug("column not found, tried %s", list(candidates))
        if numeric or pct:
            return pd.Series([np.nan] * len(df), index=df.index)
        return pd.Series([None] * len(df), index=df.index)
    series = df[col]
    if pct:
        return to_pct_series(series, expect_fraction=expect_fraction)
    return to_num_series(series) if numeric else series


# Canonical candidate lists for fields we use across views.
COLUMN_CANDIDATES: dict[str, list[str]] = {
    "ticker": ["Ticker", "Symbol"],
    "company": ["Company", "Name"],
    "sector": ["Sector"],
    "industry": ["Industry"],
    "price": ["Price"],
    "avg_volume": ["Avg Volume", "Average Volume", "AvgVolume"],
    "rel_volume": ["Rel Volume", "Relative Volume", "RelVolume"],
    "beta": ["Beta"],
    "atr": ["ATR", "Average True Range"],
    "rsi": ["RSI", "RSI (14)", "RSI(14)"],
    "gap": ["Gap"],
    "from_open": ["from Open", "Change from Open", "FromOpen"],
    "sma20": ["SMA20", "20-Day Simple Moving Average"],
    "sma50": ["SMA50", "50-Day Simple Moving Average"],
    "sma200": ["SMA200", "200-Day Simple Moving Average"],
    "high52w": ["52W High", "52-Week High"],
    "low52w": ["52W Low", "52-Week Low"],
    "perf_week": ["Perf Week", "Performance (Week)"],
    "perf_month": ["Perf Month", "Performance (Month)"],
    "perf_quarter": ["Perf Quart", "Perf Quarter", "Performance (Quarter)"],
    "perf_half": ["Perf Half Y", "Perf Half", "Performance (Half Year)"],
    "perf_year": ["Perf Year", "Performance (Year)"],
    "eps_this_y": ["EPS this Y", "EPS growth this year"],
    "eps_past5y": ["EPS past 5Y", "EPS growth past 5 years"],
    "sales_past5y": ["Sales past 5Y", "Sales growth past 5 years"],
    "change": ["Change"],
    "market_cap": ["Market Cap", "Market Capitalization"],
}


# ---------------------------------------------------------------------------
# Result container.
# ---------------------------------------------------------------------------
@dataclass
class FinvizResult:
    """Outcome of a Finviz fetch. `ok` is False on 403/empty/error; `df` is the
    (possibly empty) merged frame; `warnings` carries human-readable notes."""

    df: pd.DataFrame
    ok: bool
    warnings: list[str]

    @property
    def empty(self) -> bool:
        return self.df is None or len(self.df) == 0


# NOTE: the networked FinvizClient class (finvizfinance screener/quote access)
# was removed in the Massive migration. Live data now comes from
# pinpoint.massive_client.MassiveClient. Only the parsing utilities above remain.
