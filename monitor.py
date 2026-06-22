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
MONITOR_STATE_PATH = os.path.join(STORE_DIR, "monitor_state.json")
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

    return g("EMA5"), g("EMA10"), g("EMA20"), g("Close")


def evaluate_position(pos, price, ema5, ema10, ema20, last_close=None):
    """Return a list of alert dicts for one position. Pure / testable.

    D5 (book ch.24): the EMA trail is an exit only on a CLOSE below the EMA, not
    an intraday touch. `last_close` is the last completed daily close used to
    confirm the trail break; the live intraday `price` only raises a softer
    WATCH. The hard stop and trim/parabolic signals still use intraday `price`
    (a hard stop is a resting order; trims are intraday-extension cues)."""
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

    # EMA trail breaks (only for the position's chosen trail). Book ch.24: exit
    # on a CLOSE below the EMA; an intraday dip below is only a WATCH.
    def ema_trail(mode, ema, label):
        if trail_mode != mode or ema is None:
            return
        if last_close is not None and last_close < ema:
            add(f"{mode}_TRAIL", "HIGH", f"exit / tighten — closed below {label} trail",
                f"{tk} daily close ${last_close:,.2f} below {label} ${ema:,.2f} ({mode} trail)")
        elif price is not None and price < ema:
            add(f"{mode}_TRAIL_WATCH", "MEDIUM",
                f"watch — exit only on a daily CLOSE below {label}",
                f"{tk} ${price:,.2f} below {label} ${ema:,.2f} intraday — needs a close below to confirm")

    ema_trail("EMA10", ema10, "10 EMA")
    ema_trail("EMA20", ema20, "20 EMA")

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


# ── alert dedupe state (data/store/monitor_state.json) ────────────────────────
# Mirrors telegram_schedule's per-(ticker,kind) suppression: each alert event
# fires ONCE while a position stays OPEN. The key is cleared when the ticker
# leaves OPEN (closed/exited), so a future re-entry re-arms its alerts. This is
# the missing dedupe memory that caused the same HARD STOP to re-fire every cycle.
def _load_sent() -> set:
    data = _load_json(MONITOR_STATE_PATH, {})
    if isinstance(data, dict):
        return set(data.get("sent", []))
    return set()


def _save_sent(sent_keys) -> None:
    _atomic_write(MONITOR_STATE_PATH, {"sent": sorted(sent_keys)})


def prune_sent(sent_keys, open_tickers) -> set:
    """Drop keys for tickers no longer OPEN so a re-entry can re-alert.
    `open_tickers` is the set of currently-OPEN tickers."""
    open_set = {str(t).upper() for t in open_tickers}
    return {k for k in sent_keys if k.split(":", 1)[0] in open_set}


def select_to_send(notable, sent_keys, open_tickers) -> tuple:
    """PURE dedupe (testable). Prune keys for closed tickers, then return
    (alerts_to_send, updated_sent_keys) — skipping any (ticker, kind) already in
    the sent-set and marking each chosen alert's key as sent. Mirrors
    telegram_schedule.detect_changes, which marks an event when it is emitted."""
    sent = prune_sent(sent_keys, open_tickers)
    to_send = []
    for a in notable:
        key = f"{str(a.get('ticker', '')).upper()}:{a.get('kind')}"
        if key in sent:
            continue
        to_send.append(a)
        sent.add(key)
    return to_send, sent


def _check_pending(positions: list) -> int:
    """Promote PENDING positions to OPEN when their buy-stop entry is hit, and
    fire an entry-triggered Telegram. Returns the number promoted; rewrites
    positions.json when any change."""
    pending = [p for p in positions if str(p.get("status", "")).upper() == "PENDING"]
    if not pending:
        return 0
    from pinpoint.telegram import get_bot
    bot = get_bot()
    promoted = 0
    for pos in pending:
        tk = str(pos.get("ticker", "")).upper()
        entry = pos.get("entry")
        res = ohlcv_mod.fetch_daily(tk, cache_only=True)
        price = float(res.df["Close"].iloc[-1]) if not res.empty else None
        if price is None or not entry or price < float(entry):
            continue
        pos["status"] = "OPEN"
        pos.setdefault("entry_date", _now_iso()[:10])
        promoted += 1
        print(f"  PENDING→OPEN: {tk} (price {price:.2f} >= entry {float(entry):.2f})")
        bot.send_entry_triggered(ticker=tk, entry=float(entry), stop=float(pos.get("stop", 0) or 0),
                                 target_3r=float(pos.get("target_3r", 0) or 0),
                                 target_5r=float(pos.get("target_5r", 0) or 0),
                                 pattern=str(pos.get("setup", "")), shares=int(pos.get("shares", 0) or 0))
    if promoted:
        _atomic_write(POSITIONS_PATH, positions)
    return promoted


def main() -> int:
    positions = _load_json(POSITIONS_PATH, [])
    if not isinstance(positions, list):
        positions = []

    # 1) PENDING buy-stops: promote to OPEN + alert when their entry triggers.
    _check_pending(positions)

    # 2) exit monitoring (stop / EMA / trim) fires ONLY for positions you're
    #    actually in — status == OPEN. PENDING handled above; CLOSED skipped.
    open_positions = [p for p in positions if str(p.get("status", "")).upper() == "OPEN"]
    open_tickers = {str(p.get("ticker", "")).upper() for p in open_positions if p.get("ticker")}

    # Alert dedupe memory: drop keys for tickers no longer OPEN so a re-entry
    # re-arms (a closed position's HARD_STOP key is cleared here).
    sent_keys = prune_sent(_load_sent(), open_tickers)

    print(f"monitor: {len(open_positions)} open position(s)")
    if not open_positions:
        _save_sent(sent_keys)          # persist the pruning (clears closed positions)
        return 0

    tickers = [str(p.get("ticker", "")).upper() for p in open_positions if p.get("ticker")]
    prices = ohlcv_mod.snapshot_prices(tickers)

    new_alerts = []
    for pos in open_positions:
        tk = str(pos.get("ticker", "")).upper()
        if not tk:
            continue
        price = prices.get(tk)
        ema5, ema10, ema20, last_close = _emas_for(tk)
        if price is None and ema5 is not None:
            # No live snapshot — fall back to last close so trails still evaluate.
            price = last_close
        alerts = evaluate_position(pos, price, ema5, ema10, ema20, last_close=last_close)
        for a in alerts:
            print(f"  [{a['urgency']}] {a['kind']}: {a['message']}")
        new_alerts.extend(alerts)

    # persist (keep last MAX_ALERTS)
    existing = _load_json(ALERTS_PATH, [])
    if not isinstance(existing, list):
        existing = []
    combined = (existing + new_alerts)[-MAX_ALERTS:]
    _atomic_write(ALERTS_PATH, combined)

    # notify on CRITICAL / HIGH — but ONCE per (ticker, kind) while the position
    # stays OPEN. select_to_send prunes closed-ticker keys and skips events already
    # alerted; the sent-set is persisted so repeats are suppressed across cycles.
    notable = [a for a in new_alerts if a["urgency"] in ("CRITICAL", "HIGH")]
    to_send, sent_keys = select_to_send(notable, sent_keys, open_tickers)
    _save_sent(sent_keys)              # mark emitted (mirrors telegram_schedule)
    n_dupes = len(notable) - len(to_send)
    sent = 0
    try:
        from pinpoint.telegram import get_bot
        bot = get_bot()
    except Exception:  # noqa: BLE001
        bot = None
    for a in to_send:
        payload = {**a, "type": a.get("kind")}      # monitor uses 'kind'; bot wants 'type'
        if bot is not None and bot.enabled:
            if bot.send_position_alert(payload):
                sent += 1
        elif _send_telegram(f"⚠️ {a['urgency']} — {a['message']}\nAction: {a['action']}"):
            sent += 1

    print(f"monitor: {len(new_alerts)} alert(s) "
          f"({len(notable)} critical/high, {n_dupes} already-alerted, {sent} sent via Telegram)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
