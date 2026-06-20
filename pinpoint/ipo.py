"""ipo.py — recent-IPO initial-high tracking (spec Section 3.11).

Recent IPOs get *price discovery*: once price takes out the initial IPO high
there is no overhead resistance and moves are outsized. We:
  * find recent IPOs via Finviz's IPO Date filter, gated on price > $10 and
    avg vol >= 300k;
  * fetch full OHLCV from the IPO and store the INITIAL IPO HIGH (max high over
    the first ~5 trading days), persisted to data/ipo_highs.json;
  * fire the setup when current price approaches (within 2%) or breaks above that
    initial high on volume — the price-discovery trigger.

Names get an `is_recent_ipo` flag and an `ipo_high_break` flag so they can be
scored with the ipo_edge layer and surfaced as a separate IPO Watchlist.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .config import CONFIG
from . import ohlcv as ohlcv_mod

logger = logging.getLogger("pinpoint.ipo")

INITIAL_HIGH_BARS = 5            # "first ~5 trading days"
NEAR_FRAC = 0.02                 # within 2% of the initial high = "near"


def _store_path() -> str:
    return os.path.join(CONFIG.paths.data_dir, "ipo_highs.json")


def load_store() -> dict:
    path = _store_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read ipo store: %s", exc)
        return {}


def save_store(store: dict) -> None:
    path = _store_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2, default=str)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not write ipo store: %s", exc)


def initial_ipo_high(daily: pd.DataFrame, bars: int = INITIAL_HIGH_BARS) -> tuple[float, str]:
    """Max high over the first `bars` trading days + the first date (ISO)."""
    if daily is None or len(daily) == 0:
        return float("nan"), ""
    first = daily.iloc[:bars]
    return float(first["High"].max()), str(daily.index[0].date())


@dataclass
class IpoStatus:
    ticker: str
    sector: Optional[str] = None
    price: float = float("nan")
    ipo_high: float = float("nan")
    ipo_date: str = ""
    pct_from_high: float = float("nan")     # (price/ipo_high - 1)*100
    is_recent_ipo: bool = True
    near_high: bool = False                 # within 2% of (or above) the initial high
    ipo_high_break: bool = False            # price >= initial high
    volume_confirm: bool = False
    status: str = ""

    def as_row(self) -> dict:
        return {
            "ticker": self.ticker, "sector": self.sector, "price": round(self.price, 2),
            "ipo_high": round(self.ipo_high, 2), "ipo_date": self.ipo_date,
            "pct_from_high": round(self.pct_from_high, 1) if self.pct_from_high == self.pct_from_high else None,
            "status": self.status,
        }


def _status_label(near: bool, brk: bool) -> str:
    if brk:
        return "BREAKING OUT (price discovery)"
    if near:
        return "approaching IPO high"
    return "below IPO high"


def assess_ipo(ticker: str, sector: Optional[str], daily: pd.DataFrame,
               store: dict) -> Optional[IpoStatus]:
    """Compute/refresh an IPO's initial-high status. Uses the persisted high when
    present (so we don't recompute daily), else computes and stores it."""
    if daily is None or len(daily) < 6:
        return None

    rec = store.get(ticker)
    if rec and rec.get("ipo_high"):
        ipo_high = float(rec["ipo_high"])
        ipo_date = rec.get("ipo_date", "")
    else:
        ipo_high, ipo_date = initial_ipo_high(daily)
        store[ticker] = {"ipo_high": ipo_high, "ipo_date": ipo_date}

    if not (ipo_high == ipo_high) or ipo_high <= 0:
        return None

    price = float(daily["Close"].iloc[-1])
    pct = (price / ipo_high - 1.0) * 100.0
    near = price >= ipo_high * (1.0 - NEAR_FRAC)
    brk = price >= ipo_high
    # volume confirmation: latest volume above the recent average.
    vol_confirm = False
    if "Volume" in daily.columns and len(daily) > 25:
        vol_confirm = bool(daily["Volume"].iloc[-1] > daily["Volume"].iloc[-25:].mean())

    return IpoStatus(ticker=ticker, sector=sector, price=price, ipo_high=ipo_high,
                     ipo_date=ipo_date, pct_from_high=pct, near_high=near,
                     ipo_high_break=brk, volume_confirm=vol_confirm,
                     status=_status_label(near, brk))


@dataclass
class IpoResult:
    watchlist: pd.DataFrame
    ipo_ctx: dict = field(default_factory=dict)   # {ticker: True} for ipo_edge layer
    warnings: list[str] = field(default_factory=list)


def build_ipo_watchlist(client, limit: int = 40, ohlcv_provider=None) -> IpoResult:
    """Find recent IPOs (3.11), assess each against its initial high, persist the
    highs, and return a watchlist + an ipo_ctx for the ipo_edge scoring layer."""
    if ohlcv_provider is None:
        def ohlcv_provider(t):
            return ohlcv_mod.fetch_daily(t, period="2y").df

    warnings: list[str] = []
    # Recent IPOs come from Massive's IPO calendar (/vX/reference/ipos). Each
    # name's sector is resolved from ticker details (SIC->sector). Legacy clients
    # that can't supply IPOs degrade gracefully (no list / ipo_edge layer off).
    if hasattr(client, "get_recent_ipos"):
        recent = client.get_recent_ipos(within_days=365)
        if not recent:
            return IpoResult(watchlist=pd.DataFrame(),
                             warnings=["no recent IPOs returned by Massive calendar"])
        recent = recent[:limit]
        tickers = [r["ticker"] for r in recent]
        sectors = []
        for r in recent:
            sec = None
            if hasattr(client, "get_ticker_details"):
                try:
                    sec = client.get_ticker_details(r["ticker"]).get("sector") or None
                except Exception:  # noqa: BLE001
                    sec = None
            sectors.append(sec)
    elif hasattr(client, "fetch_universe"):
        screen = {"IPO Date": CONFIG.ipo.max_age_label, "Price": "Over $10",
                  "Average Volume": "Over 300K"}
        res = client.fetch_universe(screen, views=("overview",))
        warnings = list(res.warnings)
        if res.empty:
            return IpoResult(watchlist=pd.DataFrame(), warnings=warnings + ["no recent IPOs returned"])
        from .finviz_client import pick_column
        tcol = pick_column(res.df, ["Ticker", "Symbol"])
        scol = pick_column(res.df, ["Sector"])
        tickers = res.df[tcol].tolist()[:limit] if tcol else []
        sectors = res.df[scol].tolist()[:limit] if scol else [None] * len(tickers)
    else:
        return IpoResult(watchlist=pd.DataFrame(),
                         warnings=["IPO list unavailable (client has no IPO source)"])

    store = load_store()
    statuses: list[IpoStatus] = []
    for tk, sec in zip(tickers, sectors):
        try:
            daily = ohlcv_provider(tk)
            st = assess_ipo(tk, sec, daily, store)
            if st:
                statuses.append(st)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"IPO assess failed for {tk}: {exc}")
    save_store(store)

    # Actionable price-discovery = price HUGGING the initial high. Sort by
    # distance to the line ascending, so names just breaking / approaching rank
    # first and names that already ran far above sink to the bottom.
    def _dist(s: IpoStatus) -> float:
        return abs(s.pct_from_high) if s.pct_from_high == s.pct_from_high else 1e9
    statuses.sort(key=_dist)
    watch = pd.DataFrame([s.as_row() for s in statuses])
    ipo_ctx = {s.ticker: True for s in statuses if s.near_high or s.ipo_high_break}
    return IpoResult(watchlist=watch, ipo_ctx=ipo_ctx, warnings=warnings)
