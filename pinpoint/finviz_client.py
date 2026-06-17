"""finviz_client.py — all Finviz access (spec Section 4).

Responsibilities:
  * Robust numeric parsing of Finviz string cells (to_num).
  * Defensive column access (scraped headers vary; try several candidates).
  * Multi-view merge: the v1.3.0 custom-column screener is a no-op, so we pull
    Finviz's dedicated views (Overview/Valuation/Financial/Performance/Technical)
    and merge them on Ticker.
  * Regime inputs from the quote endpoint (SMA20/50/200 % distance).
  * Graceful handling of 403 / timeouts / empty results — warn, never crash.

This module is import-safe with no network access at import time. All network
calls are wrapped so any single failure degrades gracefully (Section 9).
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


def _is_blocked_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "403" in text or "forbidden" in text or "captcha" in text


# ---------------------------------------------------------------------------
# The client.
# ---------------------------------------------------------------------------
class FinvizClient:
    """Thin wrapper over finvizfinance with retry/backoff and view-merging.

    All network access is deferred to call time. Construct freely in tests.
    """

    def __init__(self, network=None) -> None:
        self.net = network or CONFIG.network
        self._notes: list[str] = []
        self._configured = False

    # -- low-level helpers --------------------------------------------------
    def configure(self, proxies: Optional[dict] = None) -> None:
        """Apply a browser-like User-Agent + timeout to finvizfinance's shared
        requests session (Section 4 anti-scraping). Optionally set a residential
        proxy (Phase 8). Idempotent and import-safe (lazy import)."""
        try:
            import finvizfinance.util as fvutil

            fvutil.headers["User-Agent"] = self.net.user_agent
            try:
                fvutil.session.headers.update({"User-Agent": self.net.user_agent})
            except Exception:  # noqa: BLE001 — session may be re-created internally
                pass
            if hasattr(fvutil, "set_timeout"):
                fvutil.set_timeout(max(10, int(self.net.backoff_base_s * 5)))
            if proxies and hasattr(fvutil, "set_proxy"):
                fvutil.set_proxy(proxies)
            self._configured = True
            logger.debug("finvizfinance session configured with browser UA")
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not configure finvizfinance session: %s", exc)

    def _ensure_configured(self) -> None:
        if not self._configured:
            self.configure()

    def _sleep(self) -> None:
        time.sleep(self.net.request_delay_s)

    def _retry(self, fn, label: str):
        """Run `fn` with retry/backoff; raise the last exception on exhaustion."""
        last: Optional[Exception] = None
        for attempt in range(1, self.net.max_retries + 1):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 — we deliberately catch all
                last = exc
                if _is_blocked_error(exc):
                    logger.warning("Finviz returned 403/blocked on %s (attempt %d)", label, attempt)
                    self._notes.append(f"Finviz blocked (403) while fetching {label}.")
                else:
                    logger.warning("Finviz error on %s (attempt %d): %s", label, attempt, exc)
                if attempt < self.net.max_retries:
                    time.sleep(self.net.backoff_base_s * attempt)
        if last is not None:
            raise last
        raise RuntimeError(f"unreachable retry state for {label}")

    # -- view fetch ---------------------------------------------------------
    def fetch_view(self, view_name: str, filters: dict[str, str],
                   signal: Optional[str] = None, order: Optional[str] = None,
                   ascend: bool = True) -> pd.DataFrame:
        """Fetch one Finviz screener view as a DataFrame. Imports finvizfinance
        lazily so the module is import-safe offline."""
        from finvizfinance.screener.overview import Overview
        from finvizfinance.screener.valuation import Valuation
        from finvizfinance.screener.financial import Financial
        from finvizfinance.screener.performance import Performance
        from finvizfinance.screener.technical import Technical

        view_classes = {
            "overview": Overview,
            "valuation": Valuation,
            "financial": Financial,
            "performance": Performance,
            "technical": Technical,
        }
        if view_name not in view_classes:
            raise ValueError(f"unknown Finviz view: {view_name}")
        self._ensure_configured()

        def _do() -> pd.DataFrame:
            view = view_classes[view_name]()
            filters_dict = dict(filters)
            if signal:
                view.set_filter(filters_dict=filters_dict, signal=signal)
            else:
                view.set_filter(filters_dict=filters_dict)
            kwargs: dict[str, Any] = {}
            if order:
                kwargs["order"] = order
                kwargs["ascend"] = ascend
            df = view.screener_view(**kwargs)
            return df if df is not None else pd.DataFrame()

        self._sleep()
        return self._retry(_do, f"{view_name} view")

    # -- multi-view merge ---------------------------------------------------
    def fetch_universe(self, filters: dict[str, str],
                       views: Iterable[str] = ("overview", "valuation", "performance", "technical"),
                       signal: Optional[str] = None,
                       order: Optional[str] = None, ascend: bool = True) -> FinvizResult:
        """Pull each requested view with the same filters and merge on Ticker.

        Section 4: custom columns are a no-op in v1.3.0, so we merge dedicated
        views. Any single view failing is tolerated (warn + continue).
        """
        self._notes = []
        frames: list[pd.DataFrame] = []
        ok = True

        for i, view in enumerate(views):
            try:
                # apply sort only on the first view to define ordering
                df = self.fetch_view(view, filters, signal=signal,
                                      order=order if i == 0 else None, ascend=ascend)
            except Exception as exc:  # noqa: BLE001
                ok = False
                if _is_blocked_error(exc):
                    self._notes.append(f"Finviz 403/blocked on {view} view.")
                else:
                    self._notes.append(f"Failed to fetch {view} view: {exc}")
                continue

            if df is None or len(df) == 0:
                self._notes.append(f"{view} view returned no rows.")
                continue

            tcol = pick_column(df, COLUMN_CANDIDATES["ticker"])
            if tcol is None:
                self._notes.append(f"{view} view missing a Ticker column; skipped.")
                continue
            df = df.rename(columns={tcol: "Ticker"})
            # drop duplicate non-ticker columns so the merge stays clean
            frames.append((view, df))

        if not frames:
            return FinvizResult(df=pd.DataFrame(), ok=False, warnings=self._notes)

        merged = frames[0][1]
        seen_cols = set(merged.columns)
        for _view, df in frames[1:]:
            new_cols = ["Ticker"] + [c for c in df.columns
                                     if c != "Ticker" and c not in seen_cols]
            merged = merged.merge(df[new_cols], on="Ticker", how="outer")
            seen_cols.update(new_cols)

        return FinvizResult(df=merged, ok=ok, warnings=self._notes)

    # -- regime inputs ------------------------------------------------------
    def fetch_quote_fundament(self, ticker: str) -> dict[str, Any]:
        """Return Finviz's per-ticker fundament dict (SMA20/50/200 as % distance,
        Perf fields, etc.) for regime reads. Empty dict on failure."""
        from finvizfinance.quote import finvizfinance as Quote
        self._ensure_configured()

        def _do() -> dict[str, Any]:
            return Quote(ticker).ticker_fundament()

        try:
            self._sleep()
            return self._retry(_do, f"quote {ticker}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("quote fetch failed for %s: %s", ticker, exc)
            return {}

    @property
    def notes(self) -> list[str]:
        return list(self._notes)
