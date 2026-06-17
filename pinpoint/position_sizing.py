"""position_sizing.py — the book's risk-based position math (Phase 11).

Pinpoint Trading sizes every trade by RISK, not dollars (PDF-232-234):
  - risk a fixed % of the account per trade (0.5% default; 0.1-0.2% on cold
    streaks),
  - share count = floor((account * risk%) / per-share risk),
  - keep aggregate open risk ("portfolio heat") under ~5% across 4-10 names.

All math is local and pure — no remote calls. This is a research aid, NOT
financial advice; the UI keeps the standard disclaimers.
"""

from __future__ import annotations

import json
import math
import os

from .config import CONFIG

DEFAULTS = {"account": 10_000.0, "risk_pct": 0.5, "heat_cap_pct": 5.0}


def _num(v, default=float("nan")) -> float:
    try:
        f = float(v)
        return f if f == f else default
    except (TypeError, ValueError):
        return default


def per_share_risk(entry, stop) -> float:
    """Risk per share = |entry - stop|. NaN/<=0 -> 0.0 (no valid risk)."""
    e, s = _num(entry), _num(stop)
    if e != e or s != s:
        return 0.0
    return max(0.0, abs(e - s))


def compute_shares(account, risk_pct, entry, stop) -> int:
    """Share count = floor((account * risk%/100) / per-share risk). Returns 0 when
    there's no valid risk (entry == stop, missing data, non-positive inputs)."""
    acct, rp = _num(account), _num(risk_pct)
    psr = per_share_risk(entry, stop)
    if acct <= 0 or rp <= 0 or psr <= 0:
        return 0
    dollar_risk_budget = acct * (rp / 100.0)
    return int(math.floor(dollar_risk_budget / psr))


def compute_dollar_risk(shares, entry, stop) -> float:
    """Actual $ at risk for a position = shares * per-share risk."""
    sh = _num(shares, 0.0)
    if sh <= 0:
        return 0.0
    return round(sh * per_share_risk(entry, stop), 2)


def compute_portfolio_heat(open_positions, account) -> float:
    """Aggregate open risk as a % of account. `open_positions` is a list of dicts
    each carrying either a precomputed `dollar_risk`, or `shares`+`entry`+`stop`.
    Returns 0.0 with no positions / non-positive account."""
    acct = _num(account)
    if acct <= 0 or not open_positions:
        return 0.0
    total = 0.0
    for p in open_positions:
        dr = p.get("dollar_risk")
        dr = _num(dr, float("nan"))
        if dr != dr:
            dr = compute_dollar_risk(p.get("shares"), p.get("entry"), p.get("stop"))
        total += max(0.0, dr)
    return round(total / acct * 100.0, 2)


def heat_status(heat_pct, cap_pct) -> str:
    """'green' under 80% of cap, 'amber' 80-100%, 'red' over cap."""
    cap = _num(cap_pct, 5.0)
    h = _num(heat_pct, 0.0)
    if cap <= 0:
        return "green"
    if h > cap:
        return "red"
    if h >= 0.8 * cap:
        return "amber"
    return "green"


def _paper_trades_path() -> str:
    return os.path.join(CONFIG.paths.data_dir, "paper_trades.json")


def load_open_positions() -> list:
    """Open paper trades ("would have traded", not yet resolved). Returns [] if
    data/paper_trades.json doesn't exist yet — this is the hook Watchlist v2 will
    populate. A trade is 'open' unless its status is 'closed'/'resolved'."""
    path = _paper_trades_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, list):
        return []
    return [p for p in data if isinstance(p, dict)
            and str(p.get("status", "open")).lower() not in ("closed", "resolved")]
