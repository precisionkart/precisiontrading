"""watchlist_tracker.py — backend state tracker across daily scans.

Distinct from app/views/watchlist.py (the UI page): this persists a per-ticker
status/score history so EOD scans can detect PROMOTIONS (→ STALKING) and
DEGRADATIONS (→ demoted / removed) and surface the score jump in alerts.
State lives in data/store/watchlist_tracker.json.
"""

import json
import os
import logging
from datetime import date
from dataclasses import dataclass, asdict

log = logging.getLogger("pinpoint.watchlist_tracker")
WATCHLIST_PATH = "data/store/watchlist_tracker.json"


@dataclass
class WatchlistEntry:
    ticker: str
    status: str                 # WATCHING / STALKING / OPEN / CLOSED
    score: float
    score_history: list         # last 7 daily scores
    pattern: str
    entry: float
    stop: float
    target_3r: float
    target_5r: float
    rr: float
    rs: float
    added_date: str
    promoted_date: str = ""
    notes: str = ""
    manually_added: bool = False    # True if user starred it


def load_watchlist() -> dict:
    if not os.path.exists(WATCHLIST_PATH):
        return {}
    try:
        with open(WATCHLIST_PATH) as f:
            data = json.load(f)
        out = {}
        for ticker, entry in data.items():
            # tolerate extra/missing keys across versions
            fields = WatchlistEntry.__dataclass_fields__
            clean = {k: v for k, v in entry.items() if k in fields}
            out[ticker] = WatchlistEntry(**clean)
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("Failed to load watchlist tracker: %s", e)
        return {}


def save_watchlist(watchlist: dict):
    os.makedirs(os.path.dirname(WATCHLIST_PATH), exist_ok=True)
    try:
        tmp = WATCHLIST_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump({t: asdict(e) for t, e in watchlist.items()}, f, indent=2, default=str)
        os.replace(tmp, WATCHLIST_PATH)
    except Exception as e:  # noqa: BLE001
        log.warning("Failed to save watchlist tracker: %s", e)


def _degradation_reason(scan: dict) -> str:
    score = scan.get("score", 0)
    pattern = scan.get("pattern")
    sma200 = scan.get("sma200_pct", 0)
    if sma200 and sma200 < 0:
        return "Broke below 200 SMA — avoid"
    if not pattern:
        return "Pattern invalidated"
    if score < 50:
        return f"Score dropped to {score:.0f} — setup no longer valid"
    return "Setup conditions changed"


def update_watchlist(scan_results: list, current_watchlist: dict):
    """Compare today's scan against the tracked watchlist.

    Returns (updated_watchlist, new_entries, promoted, degraded).
      - score >= 75 & was WATCHING        → PROMOTED to STALKING
      - score >= 60 & not tracked          → add as WATCHING (or STALKING if 75+)
      - score < 60 & was STALKING          → DEGRADED (demote to WATCHING)
      - score < 50 & WATCHING & not in scan → removed quietly
      - manually_added entries are never auto-removed."""
    promoted, degraded, new_entries = [], [], []
    scan_lookup = {r["ticker"]: r for r in scan_results}
    today = date.today().isoformat()

    for ticker, entry in list(current_watchlist.items()):
        if entry.status in ("OPEN", "CLOSED"):
            continue
        scan = scan_lookup.get(ticker)
        if scan is None:
            if not entry.manually_added and entry.score < 50:
                del current_watchlist[ticker]
            continue

        old_score = entry.score
        new_score = scan.get("score", 0)
        entry.score_history = (entry.score_history + [new_score])[-7:]
        entry.score = new_score
        entry.pattern = scan.get("pattern", entry.pattern)
        entry.entry = scan.get("entry", entry.entry)
        entry.stop = scan.get("stop", entry.stop)
        entry.target_3r = scan.get("target_3r", entry.target_3r)
        entry.target_5r = scan.get("target_5r", entry.target_5r)
        entry.rr = scan.get("rr", entry.rr)
        entry.rs = scan.get("rs", entry.rs)

        if entry.status == "WATCHING" and new_score >= 75 and old_score < 75:
            entry.status = "STALKING"
            entry.promoted_date = today
            promoted.append({"ticker": ticker, "old_score": old_score, "new_score": new_score,
                             "entry": entry.entry, "stop": entry.stop, "rr": entry.rr,
                             "pattern": entry.pattern, "rs": entry.rs})
        elif entry.status == "STALKING" and new_score < 60 and not entry.manually_added:
            degraded.append({"ticker": ticker, "old_score": old_score, "new_score": new_score,
                             "reason": _degradation_reason(scan)})
            entry.status = "WATCHING"

    for ticker, scan in scan_lookup.items():
        if ticker in current_watchlist:
            continue
        score = scan.get("score", 0)
        if score >= 60:
            current_watchlist[ticker] = WatchlistEntry(
                ticker=ticker, status="WATCHING" if score < 75 else "STALKING",
                score=score, score_history=[score], pattern=scan.get("pattern", ""),
                entry=scan.get("entry", 0), stop=scan.get("stop", 0),
                target_3r=scan.get("target_3r", 0), target_5r=scan.get("target_5r", 0),
                rr=scan.get("rr", 0), rs=scan.get("rs", 0), added_date=today,
                promoted_date=today if score >= 75 else "")
            new_entries.append(ticker)
            if score >= 75:
                promoted.append({"ticker": ticker, "old_score": 0, "new_score": score,
                                 "entry": scan.get("entry", 0), "stop": scan.get("stop", 0),
                                 "rr": scan.get("rr", 0), "pattern": scan.get("pattern", ""),
                                 "rs": scan.get("rs", 0)})

    return current_watchlist, new_entries, promoted, degraded
