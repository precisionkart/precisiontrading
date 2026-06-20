#!/usr/bin/env python3
"""monitor.py — intraday position monitor (cron, every 5 min during market hours).

Pinpoint is an EOD research aid; this is the one live-during-the-day helper. It
watches OPEN positions you've recorded in data/store/positions.json and surfaces
the book's trade-management signals — hard stop, EMA10/EMA20 trail breaks, the
parabolic "20%+ above 5 EMA" climax-trim, and an early "7%+ above 5 EMA with R>=3"
trim heads-up. It NEVER trades. It writes alerts to data/store/alerts.json and,
if Telegram creds are set, pings you for CRITICAL/HIGH items.

Run:  python monitor.py
Cron: */5 13-20 * * 1-5   (market hours, ET-ish; adjust to your box's clock)
"""

from __future__ import annotations

import json
import os
import sys

# Load .env early so MASSIVE_API_KEY / TELEGRAM_* are available.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pinpoint import ohlcv as ohlcv_mod  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STORE_DIR = os.path.join(HERE, "data", "store")
POSITIONS_PATH = os.path.join(STORE_DIR, "positions.json")
ALERTS_PATH = os.path.join(STORE_DIR, "alerts.json")
MAX_ALERTS = 200

PARABOLIC_PCT = 20.0   # > 20% above 5 EMA -> climax trim (book)
TRIM_PCT = 7.0         # > 7% above 5 EMA AND R>=3 -> early trim heads-up
TRIM_MIN_RR = 3.0


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def _atomic_write(path, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _now_iso() -> str:
    # Late import keeps the module import side-effect free for tests.
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _current_rr(price, entry, stop):
    """Realized reward-to-risk at the current price. None if risk is undefined."""
    try:
        risk = float(entry) - float(stop)
        if risk <= 0:
            return None
        return (float(price) - float(entry)) / risk
    except (TypeError, ValueError):
        return None


def _emas_for(ticker):
    """Return (EMA5, EMA10, EMA20) from the latest daily bar, or (None,)*3."""
    res = ohlcv_mod.fetch_daily(ticker)
    if res.empty:
        return None, None, None
    df = ohlcv_mod.add_moving_averages(res.df)
    if df is None or len(df) == 0:
        return None, None, None
    last = df.iloc[-1]

    def g(col):
        try:
            v = float(last[col])
            return v if v == v else None
        except Exception:  # noqa: BLE001
            return None

    return g("EMA5"), g("EMA10"), g("EMA20")


def evaluate_position(pos, price, ema5, ema10, ema20):
    """Return a list of alert dicts for one position. Pure / testable."""
    tk = str(pos.get("ticker", "")).upper()
    entry = pos.get("entry")
    stop = pos.get("stop")
    trail_mode = str(pos.get("trail_mode") or "").upper()
    rr = _current_rr(price, entry, stop)
    alerts = []

    def add(kind, urgency, action, msg):
        alerts.append({
            "ticker": tk, "kind": kind, "urgency": urgency, "action": action,
            "message": msg, "price": price, "ema5": ema5, "ema10": ema10,
            "ema20": ema20, "current_rr": rr, "time": _now_iso(),
        })

    if price is None:
        return alerts

    # HARD STOP — non-negotiable exit
    if stop is not None and price < float(stop):
        add("HARD_STOP", "CRITICAL", "exit full position",
            f"{tk} ${price:,.2f} below hard stop ${float(stop):,.2f}")

    # EMA trail breaks (only for the position's chosen trail)
    if trail_mode == "EMA10" and ema10 is not None and price < ema10:
        add("EMA10_TRAIL", "HIGH", "exit / tighten — closed below 10 EMA trail",
            f"{tk} ${price:,.2f} below 10 EMA ${ema10:,.2f} (EMA10 trail)")
    if trail_mode == "EMA20" and ema20 is not None and price < ema20:
        add("EMA20_TRAIL", "HIGH", "exit / tighten — closed below 20 EMA trail",
            f"{tk} ${price:,.2f} below 20 EMA ${ema20:,.2f} (EMA20 trail)")

    # PARABOLIC climax — 20%+ above 5 EMA -> trim and switch to 5 EMA trail
    if ema5 is not None and ema5 > 0:
        ext = (price / ema5 - 1.0) * 100.0
        if ext > PARABOLIC_PCT:
            add("PARABOLIC", "HIGH", "trim + switch to 5 EMA trail",
                f"{tk} {ext:.1f}% above 5 EMA — climax, trim & tighten to 5 EMA")
        elif ext > TRIM_PCT and rr is not None and rr >= TRIM_MIN_RR:
            add("TRIM_SIGNAL", "MEDIUM", "consider partial trim",
                f"{tk} {ext:.1f}% above 5 EMA at {rr:.1f}R — consider trimming")

    return alerts


def _send_telegram(text) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return False
    try:
        import requests
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, timeout=15,
                             data={"chat_id": chat, "text": text})
        return resp.ok
    except Exception as exc:  # noqa: BLE001
        print(f"  telegram send failed: {exc}")
        return False


def main() -> int:
    positions = _load_json(POSITIONS_PATH, [])
    if not isinstance(positions, list):
        positions = []
    open_positions = [p for p in positions
                      if str(p.get("status", "open")).lower() not in ("closed", "resolved")]

    print(f"monitor: {len(open_positions)} open position(s)")
    if not open_positions:
        return 0

    tickers = [str(p.get("ticker", "")).upper() for p in open_positions if p.get("ticker")]
    prices = ohlcv_mod.snapshot_prices(tickers)

    new_alerts = []
    for pos in open_positions:
        tk = str(pos.get("ticker", "")).upper()
        if not tk:
            continue
        price = prices.get(tk)
        ema5, ema10, ema20 = _emas_for(tk)
        if price is None and ema5 is not None:
            # No live snapshot — fall back to last close so trails still evaluate.
            res = ohlcv_mod.fetch_daily(tk)
            if not res.empty:
                price = float(res.df["Close"].iloc[-1])
        alerts = evaluate_position(pos, price, ema5, ema10, ema20)
        for a in alerts:
            print(f"  [{a['urgency']}] {a['kind']}: {a['message']}")
        new_alerts.extend(alerts)

    # persist (keep last MAX_ALERTS)
    existing = _load_json(ALERTS_PATH, [])
    if not isinstance(existing, list):
        existing = []
    combined = (existing + new_alerts)[-MAX_ALERTS:]
    _atomic_write(ALERTS_PATH, combined)

    # notify on CRITICAL / HIGH
    notable = [a for a in new_alerts if a["urgency"] in ("CRITICAL", "HIGH")]
    sent = 0
    for a in notable:
        if _send_telegram(f"⚠️ {a['urgency']} — {a['message']}\nAction: {a['action']}"):
            sent += 1

    print(f"monitor: {len(new_alerts)} alert(s) "
          f"({len(notable)} critical/high, {sent} sent via Telegram)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
