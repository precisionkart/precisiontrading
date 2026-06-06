"""earnings_watch.py — track earnings gap-ups and connect them to Focus (3.6 ⭐).

The earnings flag is the spec's highest-edge setup: a stock gaps up on strong
earnings (the flagpole), consolidates 1-4 weeks on light volume holding the gap,
then breaks out. build_earnings produces the daily gap-up list but it used to be
a dead end; this module persists those names and forward-tracks them so a flag
breakout 1-4 weeks later becomes a first-class scoring path.

Modeled on ipo.py (persist + forward-track). State lives in
data/earnings_watch.json, one entry per gap-up:
    {ticker, gap_date, gap_pct, initial_post_gap_high, sector, theme, status}
status: "active" (in the 1-4 week window) | "expired" (>4 weeks) | "triggered".
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from .config import CONFIG
from . import ohlcv as ohlcv_mod

logger = logging.getLogger("pinpoint.earnings_watch")

WINDOW_START_DAYS = 7        # active window opens 1 week after the gap
WINDOW_END_DAYS = 28         # ...and closes at 4 weeks
PRUNE_DAYS = 90
_DEDUP_DAYS = 5              # don't re-add the same name within this many days


def _store_path() -> str:
    return os.path.join(CONFIG.paths.data_dir, "earnings_watch.json")


def load_store() -> list[dict]:
    path = _store_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:  # noqa: BLE001
        logger.warning("earnings_watch read failed: %s", exc)
        return []


def save_store(entries: list[dict]) -> None:
    path = _store_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, default=str)
    except Exception as exc:  # noqa: BLE001
        logger.warning("earnings_watch write failed: %s", exc)


# ---------------------------------------------------------------------------
# Bull-trap filter (the earlier earnings-bug fix): keep only strong gap-ups.
# ---------------------------------------------------------------------------
def passes_gap_filter(gap: float, change: float) -> bool:
    """Gap up AND closed up AND held >= half the opening gap (no bull trap)."""
    if gap != gap or change != change:        # NaN guard
        return False
    return bool(gap > 0 and change > 0 and change >= gap * 0.5)


def _parse(d) -> Optional[date]:
    if isinstance(d, date):
        return d
    try:
        return datetime.fromisoformat(str(d)).date()
    except (ValueError, TypeError):
        try:
            return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None


# ---------------------------------------------------------------------------
# Persist / housekeeping.
# ---------------------------------------------------------------------------
def persist(candidates: list[dict], ohlcv_provider=None, today: Optional[date] = None) -> int:
    """Append today's strong gap-ups. `candidates` = list of dicts with at least
    {ticker, gap, sector, theme}; `gap` is the gap %. initial_post_gap_high is the
    gap-day (today's) high from OHLCV. Skips names already tracked in the last few
    days. Returns the count added."""
    today = today or date.today()
    if ohlcv_provider is None:
        def ohlcv_provider(t):
            return ohlcv_mod.fetch_daily(t).df

    store = load_store()
    recent = {e["ticker"] for e in store
              if (_parse(e.get("gap_date")) or date.min) >= today - timedelta(days=_DEDUP_DAYS)}
    added = 0
    for cand in candidates:
        tk = str(cand.get("ticker", "")).upper()
        if not tk or tk in recent:
            continue
        daily = ohlcv_provider(tk)
        high = float(daily["High"].iloc[-1]) if daily is not None and len(daily) else float("nan")
        store.append({
            "ticker": tk, "gap_date": today.isoformat(),
            "gap_pct": round(float(cand.get("gap", 0.0)), 2),
            "initial_post_gap_high": round(high, 2) if high == high else None,
            "sector": cand.get("sector"), "theme": cand.get("theme") or "",
            "status": "active",
        })
        added += 1
        recent.add(tk)
    if added:
        save_store(store)
    return added


def mark_expired(today: Optional[date] = None) -> None:
    today = today or date.today()
    store = load_store()
    changed = False
    for e in store:
        gd = _parse(e.get("gap_date"))
        if gd and e.get("status") == "active" and (today - gd).days > WINDOW_END_DAYS:
            e["status"] = "expired"
            changed = True
    if changed:
        save_store(store)


def mark_triggered(ticker: str, ema_zone: Optional[str] = None,
                   today: Optional[date] = None) -> None:
    today = today or date.today()
    store = load_store()
    tk = ticker.upper()
    changed = False
    for e in store:
        if e["ticker"] == tk and e.get("status") in ("active", "triggered"):
            e["status"] = "triggered"
            e["triggered_date"] = today.isoformat()
            if ema_zone:
                e["ema_zone"] = ema_zone
            changed = True
    if changed:
        save_store(store)


def prune_old(today: Optional[date] = None) -> None:
    today = today or date.today()
    store = load_store()
    kept = [e for e in store
            if (_parse(e.get("gap_date")) or today) >= today - timedelta(days=PRUNE_DAYS)]
    if len(kept) != len(store):
        save_store(kept)


# ---------------------------------------------------------------------------
# Read.
# ---------------------------------------------------------------------------
def load_active(today: Optional[date] = None) -> list[dict]:
    """Entries inside the 1-4 week window (gap_date+7 .. gap_date+28), not expired."""
    today = today or date.today()
    out = []
    for e in load_store():
        gd = _parse(e.get("gap_date"))
        if not gd or e.get("status") == "expired":
            continue
        age = (today - gd).days
        if WINDOW_START_DAYS <= age <= WINDOW_END_DAYS:
            out.append(e)
    return out


def active_ctx(today: Optional[date] = None) -> dict:
    """{ticker: entry} of active names — the lookup enrich_focus uses."""
    return {e["ticker"]: e for e in load_active(today)}


@dataclass
class EarningsWatchResult:
    watchlist: pd.DataFrame
    ctx: dict
    warnings: list


def housekeeping(today: Optional[date] = None) -> None:
    """Run the per-scan maintenance (expire + prune)."""
    mark_expired(today)
    prune_old(today)
