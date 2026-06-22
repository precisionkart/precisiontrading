"""telegram_schedule.py — NEW daily Telegram messaging system (Phase 12).

EVERYTHING here is gated behind NEW_TELEGRAM_SCHEDULE (default False) so the live
bot is unchanged until the flag is flipped. It REUSES the existing senders in
pinpoint/telegram.py (send_market_open, send_eod_scan, send_position_alert, …)
rather than duplicating them — new composed messages go through bot.send(); the
reused senders go through their own methods. A --dry-run mode prints every
message it WOULD send (capturing bot.send) without touching Telegram.

Two parts:
  A) SCHEDULED messages at fixed UK times (cron-driven): morning brief,
     1-hour / 15-min heads-ups, compact setups, market-open bell, EOD wrap,
     after-hours movers (only if any).
  B) INTRADAY "important only" change detection: diffs the current scan against
     a persisted day-state (data/telegram_state.json) and alerts ONLY on the 4
     events below, never re-alerting the same state within a day.

Flag: export NEW_TELEGRAM_SCHEDULE=true to arm live sends. Dry-run ignores it.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
from dataclasses import dataclass

from .config import CONFIG
from .telegram import get_bot

# Load .env on startup the SAME way telegram_bot.py / pinpoint/telegram.py do, so
# a bare `python -m pinpoint.telegram_schedule` picks up TELEGRAM_BOT_TOKEN /
# TELEGRAM_CHAT_ID (and NEW_TELEGRAM_SCHEDULE) with no manual `source .env`.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001 — python-dotenv optional; env may already be set
    pass

# ── master flag — live sends are OFF until this is true ───────────────────────
NEW_TELEGRAM_SCHEDULE = os.environ.get("NEW_TELEGRAM_SCHEDULE", "").strip().lower() \
    in ("1", "true", "yes", "on")

# ── TUNABLE thresholds for Part B (named constants — adjust freely) ───────────
# Safer starting defaults: deliberately hard to trip on day one so the first live
# run isn't noisy. A "big move" requires a name ALREADY on the list to shift this
# much vs the MORNING baseline; a name newly entering Focus is a NEW SETUP alert,
# not a big move (enforced in detect_changes). Tune down once you see real data.
SCORE_MOVE_ALERT = 15.0     # |Δ pinpoint_score| since the morning baseline -> alert
RS_MOVE_ALERT = 10.0        # |Δ RS| since the morning baseline -> alert
AFTER_HOURS_MOVE_PCT = 3.0  # |after-hours %move| on a watched/open name -> notable

STATE_PATH = os.path.join(CONFIG.paths.data_dir, "telegram_state.json")

_SLOTS = ("morning", "hour", "compact", "fifteen", "open", "eod", "afterhours")


def _f(x, d=0.0) -> float:
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def _today() -> str:
    return datetime.date.today().isoformat()


# ── data gathering (read-only off the saved scan cache + positions) ───────────
def gather_setups(cache):
    """Return ({ticker: setup-dict}, {tier: [setup-dict]}) from the focus list."""
    setups: dict[str, dict] = {}
    tiers: dict[str, list] = {"Elite": [], "Good": [], "Watchlist": []}
    focus = cache.lists.get("focus") if cache else None
    if focus is None or not len(focus):
        return setups, tiers
    for _, r in focus.iterrows():
        tk = str(r.get("ticker") or "")
        if not tk:
            continue
        d = {"ticker": tk, "score": _f(r.get("pinpoint_score")), "rs": _f(r.get("rs")),
             "entry": _f(r.get("entry_trigger")), "stop": _f(r.get("stop")),
             "rr": _f(r.get("reward_risk")), "price": _f(r.get("price")),
             "pattern": str(r.get("pattern") or "").split(" /")[0],
             "tier": str(r.get("tier") or ""), "sector": str(r.get("sector") or "")}
        setups[tk] = d
        if d["tier"] in tiers:
            tiers[d["tier"]].append(d)
    return setups, tiers


def _regime(cache) -> str:
    return str(getattr(cache, "regime_state", "") or "—")


def _sectors(cache) -> list:
    out = []
    for t in (getattr(cache, "themes", None) or []):
        out.append({"name": (t.get("theme") or t.get("name") or str(t))
                    if isinstance(t, dict) else str(t)})
    return out


def _positions() -> list:
    from . import store
    try:
        return store.open_positions()
    except Exception:  # noqa: BLE001
        return []


def _actionable(tiers) -> list:
    """Elite+Good setups with a valid entry/stop, best score first."""
    rows = [d for d in (tiers["Elite"] + tiers["Good"])
            if d["entry"] > 0 and d["stop"] > 0]
    return sorted(rows, key=lambda d: -d["score"])


# ── PART A composers (return message text, or None to send nothing) ───────────
def morning_brief_text(cache) -> str:
    setups, tiers = gather_setups(cache)
    pos = _positions()
    secs = _sectors(cache)
    L = ["🌅 *Morning Brief* — full scan", f"Regime: *{_regime(cache)}*"]
    if secs:
        L.append("Leaders: " + " · ".join(f"{s['name']} #{i+1}" for i, s in enumerate(secs[:3])))
    L.append(f"\n*Debrief* — {len(pos)} open position(s)" + (":" if pos else "."))
    for p in pos:
        rr = _f(p.get("current_rr"))
        emoji = "✅" if rr >= 1 else "🟡" if rr >= 0 else "🔴"
        L.append(f"{emoji} `{p.get('ticker')}` {'+' if rr >= 0 else ''}{rr:.1f}R "
                 f"(stop ${_f(p.get('stop')):.2f})")
    for tier, icon in (("Elite", "🔥"), ("Good", "⚡"), ("Watchlist", "👀")):
        rows = tiers[tier]
        L.append(f"\n{icon} *{tier}* ({len(rows)})")
        if not rows:
            L.append("_none_")
        for d in rows[:10]:
            L.append(f"`{d['ticker']}` {d['pattern']} · score {d['score']:.0f} · "
                     f"E ${d['entry']:.2f} / X ${d['stop']:.2f}"
                     + (f" · {d['rr']:.1f}:1" if d['rr'] else ""))
    L.append("\n_Market opens 2:30pm UK. Primed list at 1:30pm._")
    return "\n".join(L)


def hour_to_open_text(cache) -> str:
    _, tiers = gather_setups(cache)
    primed = _actionable(tiers)
    L = ["⏰ *1 hour to the open*", f"Regime: *{_regime(cache)}* · {len(primed)} primed setup(s)\n"]
    if not primed:
        L.append("_No primed setups today — patience._")
    for d in primed[:8]:
        L.append(f"`{d['ticker']}` — set buy stop ${d['entry']:.2f}, stop ${d['stop']:.2f}")
    return "\n".join(L)


def compact_setups_text(cache) -> str:
    _, tiers = gather_setups(cache)
    primed = _actionable(tiers)
    L = ["🎯 *Actionable setups*"]
    if not primed:
        return "🎯 *Actionable setups*\n_None today._"
    for d in primed[:12]:
        L.append(f"`{d['ticker']}`  E ${d['entry']:.2f}  X ${d['stop']:.2f}"
                 + (f"  {d['rr']:.1f}:1" if d['rr'] else ""))
    return "\n".join(L)


def fifteen_min_text(cache) -> str:
    _, tiers = gather_setups(cache)
    n = len(_actionable(tiers))
    return f"⏳ *15 minutes to the open.* {n} setup(s) primed — buy stops set?"


def after_hours_movers(cache, price_provider=None) -> list:
    """Movers on WATCHED (focus) + OPEN names with |after-hours %| >= threshold.
    price_provider(ticker) -> (last_price, pct_move) or None. Returns [] if none
    (caller sends nothing)."""
    setups, _ = gather_setups(cache)
    names = set(setups) | {str(p.get("ticker")) for p in _positions()}
    movers = []
    for tk in sorted(names):
        pm = price_provider(tk) if price_provider else None
        if not pm:
            continue
        price, pct = pm
        if abs(_f(pct)) >= AFTER_HOURS_MOVE_PCT:
            movers.append({"ticker": tk, "price": _f(price), "pct": _f(pct)})
    return movers


def after_hours_text(movers) -> str | None:
    if not movers:
        return None
    L = ["🌙 *After-hours movers*"]
    for m in movers:
        arrow = "▲" if m["pct"] >= 0 else "▼"
        L.append(f"`{m['ticker']}` {arrow} {abs(m['pct']):.1f}%  →  ${m['price']:.2f}")
    return "\n".join(L)


# ── PART B: persisted day-state + pure change detection ───────────────────────
def _empty_state(date=None) -> dict:
    return {"date": date or _today(), "baseline": {}, "focus": [],
            "alerted": {"trigger": [], "focus_new": [], "focus_dropped": [],
                        "move": [], "position": []}}


def load_state() -> dict:
    """Load the day-state; auto-resets to empty if the file is missing or stale
    (a new calendar day). Never raises."""
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            st = json.load(fh)
        if st.get("date") != _today():
            return _empty_state()
        # ensure all alerted buckets exist
        base = _empty_state(st.get("date"))
        base["baseline"] = st.get("baseline", {})
        base["focus"] = st.get("focus", [])
        for k in base["alerted"]:
            base["alerted"][k] = list(st.get("alerted", {}).get(k, []))
        return base
    except Exception:  # noqa: BLE001
        return _empty_state()


def save_state(state: dict) -> None:
    try:
        os.makedirs(CONFIG.paths.data_dir, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except Exception:  # noqa: BLE001
        pass


def establish_baseline(setups: dict) -> dict:
    """Morning baseline: snapshot today's scores/RS + focus set, clear alerts."""
    st = _empty_state()
    st["baseline"] = {tk: {"score": d["score"], "rs": d["rs"]} for tk, d in setups.items()}
    st["focus"] = sorted(setups)
    return st


def detect_changes(prev: dict, setups: dict, position_alerts: list) -> tuple[list, dict]:
    """PURE diff — returns (alert_texts, new_state). Alerts ONLY on the 4 events,
    deduped against prev['alerted'] so the same state never re-fires in a day."""
    alerted = {k: list(v) for k, v in prev.get("alerted", _empty_state()["alerted"]).items()}
    baseline = prev.get("baseline", {})
    prev_focus = set(prev.get("focus", []))
    cur_focus = set(setups)
    alerts: list[str] = []

    # 1) setup TRIGGERS — price hit/exceeded the buy-stop entry [time-critical]
    for tk in sorted(setups):
        d = setups[tk]
        if d["entry"] > 0 and d["price"] > 0 and d["price"] >= d["entry"] and tk not in alerted["trigger"]:
            alerts.append(f"✅ *TRIGGERED* `{tk}` — price ${d['price']:.2f} ≥ entry "
                          f"${d['entry']:.2f}. Stop ${d['stop']:.2f}"
                          + (f", {d['rr']:.1f}:1" if d['rr'] else "") + ". Buy-stop would fire.")
            alerted["trigger"].append(tk)

    # 2) OPEN POSITION exits — reuse monitor.py's evaluate_position output
    for pa in position_alerts:
        key = f"{pa.get('ticker')}:{pa.get('kind')}"
        if key not in alerted["position"]:
            alerts.append(f"{_pos_icon(pa)} *{pa.get('kind')}* `{pa.get('ticker')}` — "
                          f"{pa.get('message', '')} → {pa.get('action', '')}")
            alerted["position"].append(key)

    # 3) NEW Focus entry / DROP OUT of Focus [informational]
    for tk in sorted(cur_focus - prev_focus):
        if tk not in alerted["focus_new"]:
            d = setups[tk]
            alerts.append(f"➕ *NEW SETUP* `{tk}` entered Focus — {d['tier']} · "
                          f"score {d['score']:.0f} · E ${d['entry']:.2f} / X ${d['stop']:.2f}")
            alerted["focus_new"].append(tk)
    for tk in sorted(prev_focus - cur_focus):
        if tk not in alerted["focus_dropped"]:
            alerts.append(f"➖ *DROPPED* `{tk}` left the Focus list.")
            alerted["focus_dropped"].append(tk)

    # 4) BIG score/RS move vs the morning baseline [tunable thresholds].
    #    Only for names ALREADY on the list — a name that just entered Focus is a
    #    NEW SETUP alert above, never also a BIG MOVE (no double-reporting).
    for tk in sorted(cur_focus & set(baseline)):
        if tk not in prev_focus:
            continue
        d = setups[tk]
        ds = d["score"] - _f(baseline[tk].get("score"))
        dr = d["rs"] - _f(baseline[tk].get("rs"))
        if (abs(ds) >= SCORE_MOVE_ALERT or abs(dr) >= RS_MOVE_ALERT) and tk not in alerted["move"]:
            bits = []
            if abs(ds) >= SCORE_MOVE_ALERT:
                bits.append(f"score {'+' if ds >= 0 else ''}{ds:.0f} (→{d['score']:.0f})")
            if abs(dr) >= RS_MOVE_ALERT:
                bits.append(f"RS {'+' if dr >= 0 else ''}{dr:.0f} (→{d['rs']:.0f})")
            warm = "🔥 hotter" if (ds >= 0) else "🧊 cooling"
            alerts.append(f"📈 *BIG MOVE* `{tk}` {warm} — " + ", ".join(bits) + " since open.")
            alerted["move"].append(tk)

    new_state = {**prev, "date": _today(), "focus": sorted(cur_focus), "alerted": alerted}
    new_state.setdefault("baseline", baseline)
    return alerts, new_state


def _pos_icon(pa: dict) -> str:
    return {"HARD_STOP": "⛔", "EMA10_TRAIL": "⚠️", "EMA20_TRAIL": "⚠️",
            "PARABOLIC": "🚀", "TRIM_SIGNAL": "📈"}.get(str(pa.get("kind")), "📊")


def gather_position_alerts() -> list:
    """Reuse monitor.py's per-position evaluation (hard stop / EMA trail / trim /
    parabolic) so Part-B event #2 isn't a second copy of that logic."""
    try:
        import monitor  # top-level module
        from . import ohlcv as ohlcv_mod
    except Exception:  # noqa: BLE001
        return []
    out = []
    for pos in _positions():
        tk = str(pos.get("ticker") or "")
        if not tk:
            continue
        try:
            ema5, ema10, ema20, last_close = monitor._emas_for(tk)
            price = last_close
            res = ohlcv_mod.fetch_daily(tk)
            if not res.empty:
                price = float(res.df["Close"].iloc[-1])
            out.extend(monitor.evaluate_position(pos, price, ema5, ema10, ema20,
                                                 last_close=last_close))
        except Exception:  # noqa: BLE001
            continue
    return out


# ── dispatch (live = gated by the flag; dry-run = print, ignore the flag) ─────
def _send_or_print(text, dry_run: bool, bot) -> None:
    if text is None:
        if dry_run:
            print("   (nothing notable — no message sent)")
        return
    if dry_run:
        print("─" * 60)
        print(text)
        return
    bot.send(text)


def dispatch_scheduled(slot: str, dry_run: bool = False, price_provider=None) -> None:
    """Send (or preview) one scheduled message. Live sends require the flag."""
    if not dry_run and not NEW_TELEGRAM_SCHEDULE:
        return                                  # armed only when the flag is set
    from . import store
    bot = get_bot()
    cache = store.load_scan_cache()

    if slot == "open":                          # REUSE existing bell sender
        if dry_run:
            print("─" * 60)
            print("🔔 MARKET OPENNN!  (reuses telegram.send_market_open)")
        else:
            bot.send_market_open(pending=[], open_positions=_positions())
        return

    if slot == "eod":                           # REUSE existing send_eod_scan
        setups, tiers = gather_setups(cache)
        new = sorted((tiers["Elite"] + tiers["Good"]), key=lambda d: -d["score"])[:3]
        new_setups = [{"ticker": d["ticker"], "pattern": d["pattern"], "entry": d["entry"],
                       "rr": d["rr"], "score": d["score"]} for d in new]
        if dry_run:
            _patch = bot.send
            bot.send = lambda t: (print("─" * 60), print(t), True)[-1]
            try:
                bot.send_eod_scan(regime=_regime(cache), new_setups=new_setups, promoted=[],
                                  degraded=[], open_positions=_positions(), sectors=_sectors(cache))
            finally:
                bot.send = _patch
        else:
            bot.send_eod_scan(regime=_regime(cache), new_setups=new_setups, promoted=[],
                              degraded=[], open_positions=_positions(), sectors=_sectors(cache))
        return

    if slot == "morning":
        # morning also establishes the day's change-detection baseline
        setups, _ = gather_setups(cache)
        if not dry_run:
            save_state(establish_baseline(setups))
        _send_or_print(morning_brief_text(cache), dry_run, bot)
        return
    if slot == "hour":
        _send_or_print(hour_to_open_text(cache), dry_run, bot); return
    if slot == "compact":
        _send_or_print(compact_setups_text(cache), dry_run, bot); return
    if slot == "fifteen":
        _send_or_print(fifteen_min_text(cache), dry_run, bot); return
    if slot == "afterhours":
        _send_or_print(after_hours_text(after_hours_movers(cache, price_provider)), dry_run, bot)
        return
    raise ValueError(f"unknown slot {slot!r} (expected one of {_SLOTS})")


def run_intraday(dry_run: bool = False) -> list:
    """Part B — diff the current scan vs the persisted baseline; alert on the 4
    events only. Returns the alert texts (also sent live when the flag is set)."""
    if not dry_run and not NEW_TELEGRAM_SCHEDULE:
        return []
    from . import store
    cache = store.load_scan_cache()
    setups, _ = gather_setups(cache)
    prev = load_state()
    # First reference of the day (no baseline yet — e.g. the 08:00 morning brief
    # hasn't run, or state was cleared): ESTABLISH the baseline and stay SILENT,
    # so we never fire a "new setup" alert for the entire existing list. The
    # morning brief normally sets this baseline; this is the safety net.
    if not prev.get("baseline") and not prev.get("focus"):
        if not dry_run:
            save_state(establish_baseline(setups))
        return []
    alerts, new_state = detect_changes(prev, setups, gather_position_alerts())
    if not dry_run:
        save_state(new_state)
        bot = get_bot()
        for a in alerts:
            bot.send(a)
    return alerts


# ── CLI: dry-run a full simulated day ─────────────────────────────────────────
def _simulate_intraday_example(cache) -> None:
    """Show Part-B output by diffing the REAL current scan against a synthetic
    'this-morning' baseline (so we get example trigger / new / move alerts)."""
    setups, _ = gather_setups(cache)
    # synthetic baseline: pretend scores were 10 lower this morning, one extra
    # name was in focus and has since dropped, and one current name is new.
    base = {tk: {"score": max(0.0, d["score"] - 10.0), "rs": max(0.0, d["rs"] - 6.0)}
            for tk, d in setups.items()}
    focus_keys = sorted(setups)
    prev = {"date": _today(), "baseline": base,
            "focus": (focus_keys[1:] + ["ZZZZ_GONE"]) if focus_keys else ["ZZZZ_GONE"],
            "alerted": _empty_state()["alerted"]}
    alerts, _ = detect_changes(prev, setups, gather_position_alerts())
    if not alerts:
        print("   (no intraday changes vs the synthetic baseline)")
    for a in alerts:
        print("─" * 60); print(a)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NEW Telegram daily schedule (Phase 12)")
    ap.add_argument("--slot", choices=_SLOTS, help="send/preview one scheduled message")
    ap.add_argument("--intraday", action="store_true", help="run Part-B change detection")
    ap.add_argument("--dry-run", action="store_true", help="print messages, never send")
    ap.add_argument("--dry-run-all", action="store_true",
                    help="preview every scheduled message + a simulated intraday cycle")
    args = ap.parse_args(argv)

    print(f"NEW_TELEGRAM_SCHEDULE = {NEW_TELEGRAM_SCHEDULE} | "
          f"SCORE_MOVE_ALERT={SCORE_MOVE_ALERT} RS_MOVE_ALERT={RS_MOVE_ALERT} "
          f"AFTER_HOURS_MOVE_PCT={AFTER_HOURS_MOVE_PCT}\n")

    if args.dry_run_all:
        from . import store
        cache = store.load_scan_cache()
        titles = {"morning": "08:00 UK — MORNING BRIEF", "hour": "13:30 UK — 1 HOUR TO OPEN",
                  "compact": "14:00 UK — COMPACT SETUPS", "fifteen": "14:15 UK — 15 MIN TO OPEN",
                  "open": "14:30 UK — MARKET OPEN BELL", "eod": "21:05 UK — EOD WRAP",
                  "afterhours": "22:00 UK — AFTER-HOURS MOVERS"}
        for slot in _SLOTS:
            print(f"\n========== {titles[slot]} ==========")
            dispatch_scheduled(slot, dry_run=True)
        print("\n========== INTRADAY CHANGE DETECTION (simulated cycle) ==========")
        _simulate_intraday_example(cache)
        return 0

    if args.intraday:
        for a in run_intraday(dry_run=args.dry_run):
            print("─" * 60); print(a)
        return 0
    if args.slot:
        dispatch_scheduled(args.slot, dry_run=args.dry_run)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
