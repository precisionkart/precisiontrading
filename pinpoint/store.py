"""store.py — persistence for the web app (spec Section 7B).

Three things live here, all under data/store/:
  * watchlist.json          — tickers the user saved (for the Morning Dashboard)
  * universe_latest.parquet — the last full-scan universe snapshot, used as the
    RS reference universe for the My-Picks analyzer (RS is meaningless for a few
    pasted tickers; it must be ranked against a broad universe).
  * cache/                  — the most recent scan's lists + regime, so the
    Morning Dashboard can render instantly without re-hitting Finviz.

Everything is best-effort and never raises into the caller (Section 9).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import pandas as pd

from .config import CONFIG

logger = logging.getLogger("pinpoint.store")


def _dir() -> str:
    d = CONFIG.paths.store_dir
    os.makedirs(d, exist_ok=True)
    return d


def _cache_dir() -> str:
    d = os.path.join(_dir(), "cache")
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Watchlist.
# ---------------------------------------------------------------------------
def _watchlist_path() -> str:
    return os.path.join(_dir(), "watchlist.json")


def load_watchlist() -> list[str]:
    """Ticker list (back-compat). The rich board lives in pinpoint/watchlist.py."""
    from . import watchlist as wl_mod
    return wl_mod.tickers()


def save_watchlist(tickers: list[str]) -> None:
    """Back-compat setter — preserves existing metadata for kept names, drops
    removed ones, adds new ones with today's added_date."""
    from . import watchlist as wl_mod
    want = list(dict.fromkeys(t.upper() for t in tickers))
    existing = {e["ticker"]: e for e in wl_mod.load_entries()}
    from datetime import date as _date
    entries = []
    for t in want:
        entries.append(existing.get(t, {"ticker": t, "added_date": _date.today().isoformat(),
                                         "notes": "", "last_status": None}))
    wl_mod.save_entries(entries)


def _user_settings_path() -> str:
    return os.path.join(CONFIG.paths.data_dir, "user_settings.json")


def load_user_settings() -> dict:
    """Persisted user settings (position-sizing config). Falls back to defaults
    for any missing/invalid key."""
    from .position_sizing import DEFAULTS
    out = dict(DEFAULTS)
    path = _user_settings_path()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for k in DEFAULTS:
                if k in data:
                    try:
                        out[k] = float(data[k])
                    except (TypeError, ValueError):
                        pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("user settings read failed: %s", exc)
    return out


def save_user_settings(settings: dict) -> None:
    from .position_sizing import DEFAULTS
    merged = load_user_settings()
    for k in DEFAULTS:
        if k in settings:
            try:
                merged[k] = float(settings[k])
            except (TypeError, ValueError):
                pass
    try:
        os.makedirs(CONFIG.paths.data_dir, exist_ok=True)
        with open(_user_settings_path(), "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)
    except Exception as exc:  # noqa: BLE001
        logger.warning("user settings write failed: %s", exc)


# ---------------------------------------------------------------------------
# Open positions (data/store/positions.json) — the SAME file monitor.py reads
# for its 5-minute alert cron. Field names here must stay compatible with it
# (ticker / entry / stop / shares / trail_mode / status).
# ---------------------------------------------------------------------------
def _positions_path() -> str:
    return os.path.join(CONFIG.paths.store_dir, "positions.json")


def load_positions() -> list[dict]:
    path = _positions_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as exc:  # noqa: BLE001
        logger.warning("positions read failed: %s", exc)
        return []


def open_positions() -> list[dict]:
    """Positions whose status is not closed/resolved (mirrors monitor.py)."""
    return [p for p in load_positions()
            if str(p.get("status", "open")).lower() not in ("closed", "resolved")]


def add_position(entry: dict) -> list[dict]:
    """Append one position (atomic) to positions.json. Dedups an existing OPEN
    row for the same ticker (re-placing overwrites it)."""
    positions = [p for p in load_positions()
                 if not (str(p.get("ticker", "")).upper() == str(entry.get("ticker", "")).upper()
                         and str(p.get("status", "open")).lower() not in ("closed", "resolved"))]
    positions.append(entry)
    path = _positions_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(positions, f, indent=2)
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("positions write failed: %s", exc)
    return positions


def add_to_watchlist(ticker: str) -> list[str]:
    from . import watchlist as wl_mod
    return wl_mod.add(ticker)


def remove_from_watchlist(ticker: str) -> list[str]:
    from . import watchlist as wl_mod
    return wl_mod.remove(ticker)


# ---------------------------------------------------------------------------
# Universe snapshot (RS reference for My Picks).
# ---------------------------------------------------------------------------
def _universe_path() -> str:
    return os.path.join(_dir(), "universe_latest.parquet")


def _universe_meta_path() -> str:
    return os.path.join(_dir(), "universe_latest.json")


def save_universe_snapshot(universe: pd.DataFrame, when: Optional[str] = None) -> Optional[str]:
    """Persist the normalized universe used for RS ranking (My-Picks reference)."""
    if universe is None or len(universe) == 0:
        return None
    when = when or date.today().isoformat()
    try:
        universe.to_parquet(_universe_path())
        with open(_universe_meta_path(), "w", encoding="utf-8") as f:
            json.dump({"date": when, "rows": int(len(universe))}, f)
        return _universe_path()
    except Exception as exc:  # noqa: BLE001
        logger.warning("universe snapshot write failed: %s", exc)
        return None


def load_universe_snapshot() -> tuple[Optional[pd.DataFrame], Optional[str]]:
    """Return (universe, date) or (None, None) if no snapshot exists."""
    if not os.path.exists(_universe_path()):
        return None, None
    try:
        df = pd.read_parquet(_universe_path())
        when = None
        if os.path.exists(_universe_meta_path()):
            with open(_universe_meta_path(), encoding="utf-8") as f:
                when = json.load(f).get("date")
        return df, when
    except Exception as exc:  # noqa: BLE001
        logger.warning("universe snapshot read failed: %s", exc)
        return None, None


# ---------------------------------------------------------------------------
# Scan cache (instant Morning Dashboard).
# ---------------------------------------------------------------------------
_CACHE_LISTS = ("focus", "targets", "earnings", "earnings_down", "ipo")


@dataclass
class ScanCache:
    lists: dict = field(default_factory=dict)     # name -> DataFrame
    regime_state: str = "neutral"
    regime_rationale: list = field(default_factory=list)
    themes: list = field(default_factory=list)    # [{theme,rank,score}, ...]
    as_of: str = ""
    date: str = ""

    @property
    def is_today(self) -> bool:
        return self.date == date.today().isoformat()


def _themes_list(theme_rank) -> list:
    if not theme_rank:
        return []
    return [{"theme": t, "rank": r["rank"], "score": r["score"]}
            for t, r in sorted(theme_rank.items(), key=lambda kv: kv[1]["rank"])]


def save_scan_cache(lists: dict, regime, as_of: str, theme_rank=None) -> None:
    """Persist the scan lists + regime + theme rankings so the dashboard renders
    without a refetch."""
    cdir = _cache_dir()
    try:
        for name in _CACHE_LISTS:
            df = lists.get(name)
            path = os.path.join(cdir, f"{name}.parquet")
            if df is not None and len(df):
                df.to_parquet(path)
            elif os.path.exists(path):
                os.remove(path)
        meta = {"as_of": as_of, "date": date.today().isoformat(),
                "regime_state": getattr(regime, "state", "neutral"),
                "regime_rationale": getattr(regime, "rationale", []) or [],
                "themes": _themes_list(theme_rank)}
        with open(os.path.join(cdir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    except Exception as exc:  # noqa: BLE001
        logger.warning("scan cache write failed: %s", exc)


def _records(df) -> list:
    """DataFrame -> JSON-safe list of records (NaN -> None)."""
    if df is None or len(df) == 0:
        return []
    return df.where(pd.notna(df), None).to_dict("records")


def published_path() -> str:
    """The single committed blob the read-only cloud app reads (Phase 8)."""
    return os.path.join(CONFIG.paths.data_dir, "latest_scan.json")


def save_published_scan(regime, themes_ranked: dict, lists: dict, as_of_et: str,
                        as_of_utc: str, index_levels: Optional[dict] = None,
                        as_of_mode: str = "full") -> Optional[str]:
    """Serialize a full scan to data/latest_scan.json for the read-only cloud app.

    `lists` maps name -> DataFrame (focus/targets/earnings/earnings_down/ipo).
    `themes_ranked` is theme_ctx.theme_rank. `index_levels` is the SPY/QQQ/IWM/DIA
    snapshot for the Pre-Market Briefing (Phase 10 step 9). Everything is plain
    JSON so no engine runs on cloud to render the dashboard."""
    from datetime import date as _date
    payload = {
        "date": _date.today().isoformat(),
        "as_of_et": as_of_et, "as_of_utc": as_of_utc, "as_of_mode": as_of_mode,
        "regime": {"state": getattr(regime, "state", "neutral"),
                   "rationale": getattr(regime, "rationale", []) or [],
                   "exposure_label": getattr(regime, "exposure_label", "One-Third")},
        "index_levels": index_levels or {},
        "themes": [{"theme": t, "rank": r["rank"], "score": r["score"]}
                   for t, r in sorted((themes_ranked or {}).items(),
                                      key=lambda kv: kv[1]["rank"])],
        "lists": {name: _records(df) for name, df in lists.items()},
    }
    path = published_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning("publish write failed: %s", exc)
        return None


def load_published_scan(path: Optional[str] = None) -> Optional[dict]:
    """Read the committed latest_scan.json (read-only cloud path). Returns the
    raw payload dict, or None if absent/unreadable."""
    path = path or published_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:  # noqa: BLE001
        logger.warning("publish read failed: %s", exc)
        return None


def load_scan_cache() -> Optional[ScanCache]:
    cdir = _cache_dir()
    meta_path = os.path.join(cdir, "meta.json")
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        lists = {}
        for name in _CACHE_LISTS:
            path = os.path.join(cdir, f"{name}.parquet")
            lists[name] = pd.read_parquet(path) if os.path.exists(path) else pd.DataFrame()
        return ScanCache(lists=lists, regime_state=meta.get("regime_state", "neutral"),
                         regime_rationale=meta.get("regime_rationale", []),
                         themes=meta.get("themes", []),
                         as_of=meta.get("as_of", ""), date=meta.get("date", ""))
    except Exception as exc:  # noqa: BLE001
        logger.warning("scan cache read failed: %s", exc)
        return None
