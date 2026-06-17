"""watchlist.py — the trading board (Phase 11 Watchlist v2).

Three local JSON files, all written atomically (.tmp + os.replace):
  - watchlist.json          list of {ticker, added_date, notes, last_status}
  - watchlist_history.json  {ticker: {added_date, history:[{date,price,rs,score,
                            status,pattern,source}]}} — the 60-day paper-track
  - paper_trades.json       list of trade records (entry/stop/target/shares/
                            status/resolution)

Local-only, per-device. Pure data + small deterministic logic; no remote calls.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date

from .config import CONFIG
from . import position_sizing as sizing

NOTES_MAX = 280
DORMANT_CONSECUTIVE_DAYS = 14          # 14+ days off all scan lists -> "consider removing"
PAPER_MAX_AGE_DAYS = 28                # 4 weeks -> OPEN_AGED


# --------------------------------------------------------------------------- io
def _path(name: str) -> str:
    return os.path.join(CONFIG.paths.data_dir, name)


def _read(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def _atomic_write(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


def _today(today: date | None = None) -> str:
    return (today or date.today()).isoformat()


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------- entries (board)
def load_entries() -> list[dict]:
    """Watchlist entries with metadata. Back-compatible with the legacy list-of-
    strings format."""
    data = _read(_path("watchlist.json"), [])
    out, seen = [], set()
    for e in data if isinstance(data, list) else []:
        if isinstance(e, str):
            tk, meta = e.upper(), {}
        elif isinstance(e, dict) and e.get("ticker"):
            tk, meta = str(e["ticker"]).upper(), e
        else:
            continue
        if tk in seen:
            continue
        seen.add(tk)
        out.append({"ticker": tk, "added_date": meta.get("added_date"),
                    "notes": (meta.get("notes") or "")[:NOTES_MAX],
                    "last_status": meta.get("last_status")})
    return out


def save_entries(entries: list[dict]) -> None:
    _atomic_write(_path("watchlist.json"), entries)


def tickers() -> list[str]:
    return [e["ticker"] for e in load_entries()]


def is_member(ticker: str) -> bool:
    return ticker.strip().upper() in set(tickers())


def add(ticker: str, today: date | None = None) -> list[str]:
    t = ticker.strip().upper()
    ents = load_entries()
    if t and t not in {e["ticker"] for e in ents}:
        ents.append({"ticker": t, "added_date": _today(today), "notes": "", "last_status": None})
        save_entries(ents)
    return [e["ticker"] for e in ents]


def remove(ticker: str) -> list[str]:
    """Remove the entry; history is intentionally PRESERVED (don't delete the
    paper-track on un-star)."""
    t = ticker.strip().upper()
    ents = [e for e in load_entries() if e["ticker"] != t]
    save_entries(ents)
    return [e["ticker"] for e in ents]


def set_notes(ticker: str, notes: str) -> None:
    t = ticker.strip().upper()
    ents = load_entries()
    for e in ents:
        if e["ticker"] == t:
            e["notes"] = (notes or "")[:NOTES_MAX]
    save_entries(ents)


def set_status(ticker: str, status: str) -> None:
    t = ticker.strip().upper()
    ents = load_entries()
    for e in ents:
        if e["ticker"] == t:
            e["last_status"] = status
    save_entries(ents)


def days_on(ticker: str, today: date | None = None) -> int | None:
    """Days since added (from the entry's added_date, else first history date)."""
    t = ticker.strip().upper()
    added = None
    for e in load_entries():
        if e["ticker"] == t:
            added = e.get("added_date")
    if not added:
        hist = load_history().get(t, {})
        added = hist.get("added_date") or (hist.get("history", [{}])[0].get("date")
                                           if hist.get("history") else None)
    if not added:
        return None
    try:
        return (date.fromisoformat(_today(today)) - date.fromisoformat(added[:10])).days
    except (ValueError, TypeError):
        return None


# ----------------------------------------------------------------- status compute
def compute_status(ticker: str, focus_df=None, targets_df=None, earnings_ctx=None) -> str:
    """One of ACTIVE / EARNINGS / WATCH / DORMANT for a name, given today's scan
    lists. ACTIVE = in Focus with R:R >= the methodology floor."""
    t = ticker.strip().upper()
    floor = CONFIG.entry.min_reward_risk
    if focus_df is not None and len(focus_df) and "ticker" in getattr(focus_df, "columns", []):
        hit = focus_df[focus_df["ticker"].astype(str).str.upper() == t]
        if len(hit):
            rr = _num(hit.iloc[0].get("reward_risk"))
            if rr is not None and rr >= floor:
                return "ACTIVE"
    if earnings_ctx and t in {str(k).upper() for k in earnings_ctx}:
        return "EARNINGS"
    if targets_df is not None and len(targets_df) and "ticker" in getattr(targets_df, "columns", []):
        if (targets_df["ticker"].astype(str).str.upper() == t).any():
            return "WATCH"
    return "DORMANT"


def dormant_streak(ticker: str) -> int:
    """Trailing consecutive history snapshots with status DORMANT (proxy for days
    off all scan lists). >= DORMANT_CONSECUTIVE_DAYS -> 'consider removing'."""
    hist = load_history().get(ticker.strip().upper(), {}).get("history", [])
    streak = 0
    for row in reversed(hist):
        if str(row.get("status", "")).upper() == "DORMANT":
            streak += 1
        else:
            break
    return streak


# ----------------------------------------------------------------- history (snapshots)
def load_history() -> dict:
    return _read(_path("watchlist_history.json"), {})


def save_history(h: dict) -> None:
    _atomic_write(_path("watchlist_history.json"), h)


def snapshot(rows: list[dict], source: str = "cron", today: date | None = None) -> None:
    """Append one daily history row per ticker. Idempotent on (ticker, date) —
    a same-date row is replaced, so re-running on the same day never duplicates."""
    h = load_history()
    d = _today(today)
    for r in rows:
        tk = str(r.get("ticker", "")).upper()
        if not tk:
            continue
        rec = h.setdefault(tk, {"added_date": d, "history": []})
        rec.setdefault("history", [])
        rec["history"] = [x for x in rec["history"] if x.get("date") != d]   # idempotent
        rec["history"].append({"date": d, "price": _num(r.get("price")),
                               "rs": _num(r.get("rs")), "score": _num(r.get("score")),
                               "status": r.get("status"), "pattern": r.get("pattern"),
                               "source": source})
        rec["history"].sort(key=lambda x: x.get("date", ""))
    save_history(h)


def rs_series(ticker: str, n: int = 30) -> list[float]:
    hist = load_history().get(ticker.strip().upper(), {}).get("history", [])
    vals = [x.get("rs") for x in hist[-n:] if isinstance(x.get("rs"), (int, float)) and x.get("rs") == x.get("rs")]
    return [float(v) for v in vals]


def since_added_pct(ticker: str) -> float | None:
    """% change first-snapshot price -> latest-snapshot price."""
    hist = load_history().get(ticker.strip().upper(), {}).get("history", [])
    prices = [x.get("price") for x in hist if isinstance(x.get("price"), (int, float)) and x.get("price") == x.get("price") and x.get("price") > 0]
    if len(prices) < 2:
        return None
    return round((prices[-1] / prices[0] - 1.0) * 100.0, 1)


# ----------------------------------------------------------------- paper trades
def load_trades() -> list[dict]:
    data = _read(_path("paper_trades.json"), [])
    return [t for t in data if isinstance(t, dict)] if isinstance(data, list) else []


def save_trades(trades: list[dict]) -> None:
    _atomic_write(_path("paper_trades.json"), trades)


def mark_paper_trade(ticker: str, entry, stop, target, reward_risk, shares,
                     today: date | None = None) -> dict:
    """Record a 'would have traded' entry at the current plan. Returns the trade."""
    tr = {"trade_id": uuid.uuid4().hex, "ticker": ticker.strip().upper(),
          "marked_date": _today(today), "entry": _num(entry), "stop": _num(stop),
          "target": _num(target), "reward_risk": _num(reward_risk),
          "shares": int(shares) if shares else 0,
          "risk_dollars": sizing.compute_dollar_risk(shares, entry, stop),
          "status": "open", "resolved_date": None, "exit_price": None,
          "exit_reason": None, "r_achieved": None}
    trades = load_trades()
    trades.append(tr)
    save_trades(trades)
    return tr


def resolve_trades(ohlcv_provider, today: date | None = None) -> list[dict]:
    """Resolve open paper-trades against today's OHLCV. IDEMPOTENT: resolved
    trades are skipped, and win/loss exits use the fixed target/stop prices (not
    the bar's high/low), so re-running the same day yields identical results.
      - today's HIGH >= target -> WIN  (exit=target, r=reward_risk)
      - today's LOW  <= stop   -> LOSS (exit=stop,   r=-1)
      - else open; >= 28 days old -> OPEN_AGED (still open, visually flagged)."""
    trades = load_trades()
    d = _today(today)
    changed = False
    for tr in trades:
        if str(tr.get("status")) not in ("open", "open_aged"):
            continue
        daily = ohlcv_provider(tr["ticker"]) if ohlcv_provider else None
        if daily is not None and len(daily):
            last = daily.iloc[-1]
            hi, lo = _num(last.get("High")), _num(last.get("Low"))
            tgt, stp = _num(tr.get("target")), _num(tr.get("stop"))
            if tgt is not None and hi is not None and hi >= tgt:
                tr.update(status="won", resolved_date=d, exit_price=tgt,
                          exit_reason="target_hit", r_achieved=tr.get("reward_risk"))
                changed = True
                continue
            if stp is not None and lo is not None and lo <= stp:
                tr.update(status="lost", resolved_date=d, exit_price=stp,
                          exit_reason="stop_hit", r_achieved=-1.0)
                changed = True
                continue
        # still open — age flag
        try:
            age = (date.fromisoformat(d) - date.fromisoformat(str(tr["marked_date"])[:10])).days
        except (ValueError, TypeError, KeyError):
            age = 0
        if age >= PAPER_MAX_AGE_DAYS and tr.get("status") == "open":
            tr["status"] = "open_aged"
            changed = True
    if changed:
        save_trades(trades)
    return trades


def paper_stats(trades: list[dict] | None = None) -> dict:
    """Running stats: counts, win rate, average R."""
    trades = load_trades() if trades is None else trades
    won = [t for t in trades if t.get("status") == "won"]
    lost = [t for t in trades if t.get("status") == "lost"]
    open_ = [t for t in trades if str(t.get("status")) in ("open", "open_aged")]
    resolved = won + lost
    rs = [t.get("r_achieved") for t in resolved if isinstance(t.get("r_achieved"), (int, float))]
    return {"total": len(trades), "open": len(open_), "won": len(won), "lost": len(lost),
            "win_rate": round(len(won) / len(resolved) * 100.0, 1) if resolved else None,
            "avg_r": round(sum(rs) / len(rs), 2) if rs else None}
