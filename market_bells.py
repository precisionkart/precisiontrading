#!/usr/bin/env python3
"""market_bells.py — market open (2:30pm UK) and close (9:00pm UK) Telegram pings.

Reads positions from data/store/positions.json and current price/10-EMA from the
OHLCV cache only (no live API calls). Run via cron:
  python market_bells.py open
  python market_bells.py close
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass

from pinpoint.telegram import get_bot
from pinpoint import ohlcv
from pinpoint.ohlcv import add_moving_averages

POS_PATH = "data/store/positions.json"


def get_position_status(positions: list) -> list:
    """Current R + exit signal per open position, from the OHLCV cache only."""
    enriched = []
    for pos in positions:
        if pos.get("status") != "OPEN":
            continue
        entry = pos.get("entry", 0) or 0
        stop = pos.get("stop", 0) or 0
        risk = entry - stop
        result = ohlcv.fetch_daily(str(pos.get("ticker", "")).upper(), cache_only=True)
        if result.empty or risk <= 0:
            enriched.append({**pos, "current_rr": 0, "exit_signal": "holding"})
            continue
        df = add_moving_averages(result.df)
        price = float(df["Close"].iloc[-1])
        ema10 = float(df["EMA10"].iloc[-1])
        current_rr = round((price - entry) / risk, 2)
        if price < ema10:
            exit_signal = "below 10 EMA ⚠️"
        elif stop and price < stop * 1.02:
            exit_signal = "near stop 🔴"
        elif current_rr >= 3:
            exit_signal = "trim zone 📈"
        else:
            exit_signal = "holding"
        enriched.append({**pos, "current_rr": current_rr, "exit_signal": exit_signal})
    return enriched


def _load_positions() -> list:
    if not os.path.exists(POS_PATH):
        return []
    try:
        with open(POS_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "open"
    bot = get_bot()
    if not bot.enabled:
        print("Telegram not configured")
        return

    positions = _load_positions()
    open_positions = get_position_status([p for p in positions if p.get("status") == "OPEN"])
    pending = [p for p in positions if p.get("status") == "PENDING"]

    if mode == "open":
        bot.send_market_open(pending=pending, open_positions=open_positions)
        print("✅ Market open alert sent")
    elif mode == "close":
        import datetime
        today = datetime.date.today().isoformat()
        closed_today = [p for p in positions if str(p.get("exit_date", ""))[:10] == today]
        bot.send_market_close(open_positions=open_positions, closed_today=closed_today)
        print("✅ Market close alert sent")
    else:
        print(f"unknown mode '{mode}' (use 'open' or 'close')")


if __name__ == "__main__":
    main()
