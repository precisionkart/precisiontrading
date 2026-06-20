#!/usr/bin/env python3
"""Pinpoint Telegram Bot — standalone, polling-based (NOT part of the Streamlit app).

Run:  python telegram_bot.py

In your Precision Trading channel type:
  $AAPL                      → full breakdown
  $AAPL vs $NVDA             → comparison
  $AAPL entry 295 stop 280   → custom position size
  /regime · /watchlist · /positions · /help

Read-only: it never writes positions.json or any data file. Reads the OHLCV cache
(and account size from data/store/user_settings.json) the same way the dashboard does.
"""

import os
import re
import sys
import time
import logging

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pinpoint.bot")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
BASE = f"https://api.telegram.org/bot{TOKEN}"
SETTINGS_PATH = "data/store/user_settings.json"


# ── Telegram helpers ───────────────────────────────────────
def get_updates(offset: int = 0) -> list:
    try:
        resp = requests.get(f"{BASE}/getUpdates",
                            params={"offset": offset, "timeout": 30}, timeout=35)
        return resp.json().get("result", [])
    except Exception as e:  # noqa: BLE001
        log.warning("getUpdates failed: %s", e)
        return []


def send(text: str, reply_to: int = None) -> bool:
    try:
        payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        resp = requests.post(f"{BASE}/sendMessage", json=payload, timeout=10)
        return resp.json().get("ok", False)
    except Exception as e:  # noqa: BLE001
        log.warning("send failed: %s", e)
        return False


def typing():
    try:
        requests.post(f"{BASE}/sendChatAction",
                      json={"chat_id": CHAT_ID, "action": "typing"}, timeout=5)
    except Exception:  # noqa: BLE001
        pass


def _account_settings() -> tuple:
    """(account_size, risk_pct) from user_settings.json — tolerant of the
    'account'/'account_size' key naming. Falls back to 100000 / 0.5."""
    import json
    account, risk = 100_000, 0.5
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH) as f:
                s = json.load(f)
            account = s.get("account_size", s.get("account", 100_000))
            risk = s.get("risk_pct", 0.5)
        except Exception:  # noqa: BLE001
            pass
    return float(account), float(risk)


# ── Pinpoint analysis ──────────────────────────────────────
def analyse_ticker(ticker: str) -> str:
    ticker = ticker.upper().strip()
    try:
        from pinpoint import ohlcv as ohlcv_mod
        from pinpoint import patterns as patterns_mod
        from pinpoint import entries as entries_mod
        from pinpoint import stage_trend as stage_mod
        from pinpoint.ohlcv import add_moving_averages

        result = ohlcv_mod.fetch_daily(ticker, use_cache=True)
        if result.empty:
            return f"❌ *{ticker}* — no data found. Check the ticker symbol."

        df = add_moving_averages(result.df)
        last, prev = df.iloc[-1], df.iloc[-2]
        price = float(last["Close"])
        change_pct = (price - float(prev["Close"])) / float(prev["Close"]) * 100
        change_emoji = "▲" if change_pct >= 0 else "▼"

        sma200 = float(last.get("SMA200", 0)) if "SMA200" in df.columns else 0
        sma50 = float(last.get("SMA50", 0)) if "SMA50" in df.columns else 0
        ema20 = float(last.get("EMA20", 0)) if "EMA20" in df.columns else 0
        sma200_pct = (price - sma200) / sma200 * 100 if sma200 else 0
        sma50_pct = (price - sma50) / sma50 * 100 if sma50 else 0
        sma20_pct = (price - ema20) / ema20 * 100 if ema20 else 0
        stage = stage_mod.classify_from_sma(sma20_pct, sma50_pct, sma200_pct)

        pat = patterns_mod.best_pattern(df, require_measured=True)
        setup = entries_mod.compute_setup(pat.trigger, pat.support_low, pat.measured_target) if pat else None

        high_52w = float(df["High"].tail(252).max())
        low_52w = float(df["Low"].tail(252).min())
        pct_from_high = (price - high_52w) / high_52w * 100
        avg_vol = float(df["Volume"].tail(20).mean())
        rvol = float(df["Volume"].iloc[-1]) / avg_vol if avg_vol > 0 else 1.0
        adr = float(((df["High"] - df["Low"]) / df["Close"]).tail(14).mean() * 100)
        account_size, risk_pct = _account_settings()

        arrow = "🟢" if change_pct >= 0 else "🔴"
        lines = [f"📊 *{ticker}*\n${price:.2f} {change_emoji}{abs(change_pct):.2f}%  {arrow}", "",
                 f"*Stage:* {stage.label}"]
        ma_lines = []
        if sma200 > 0:
            ma_lines.append(f"{'✅' if price > sma200 else '❌'} 200 SMA ${sma200:.2f}")
        if sma50 > 0:
            ma_lines.append(f"{'✅' if price > sma50 else '❌'} 50 SMA ${sma50:.2f}")
        if ema20 > 0:
            ma_lines.append(f"{'✅' if price > ema20 else '⚠️'} 20 EMA ${ema20:.2f}")
        if ma_lines:
            lines.append("*MAs:* " + "  ·  ".join(ma_lines))
        lines += ["", f"*52W:* High ${high_52w:.2f} · Low ${low_52w:.2f}\n"
                      f"       {pct_from_high:.1f}% from high",
                  f"*RVOL:* {rvol:.2f}x  ·  *ADR:* {adr:.1f}%", ""]

        if pat:
            bar = "█" * int(pat.confidence * 10) + "░" * (10 - int(pat.confidence * 10))
            lines.append(f"*Pattern:* {pat.label}\nConfidence: {bar} {pat.confidence:.0%}")
        else:
            lines.append("*Pattern:* None detected")
        lines.append("")

        if setup and setup.rr_ok:
            dollar_risk = account_size * (risk_pct / 100)
            risk_ps = setup.entry - setup.stop
            shares = int(dollar_risk / risk_ps) if risk_ps > 0 else 0
            lines += ["*Setup:*",
                      f"E `${setup.entry:.2f}` · X `${setup.stop:.2f}` · T `${setup.measured_target:.2f}`",
                      f"R:R {setup.reward_risk:.1f}:1 ✅  ·  {shares} sh  ·  ${dollar_risk:,.0f} risk",
                      f"Stop type: {setup.stop_kind} (.89 rule)"]
        elif setup:
            lines += [f"*Setup:* R:R {setup.reward_risk:.1f}:1 ⚠️ (below 5:1 minimum)",
                      f"E `${setup.entry:.2f}` · X `${setup.stop:.2f}`"]
        else:
            lines.append("*Setup:* No measured pattern — enter levels manually")
        lines.append("")

        if not stage.is_stage2:
            verdict = f"⚠️ *WATCHLIST* — {stage.label}, not Stage 2"
        elif pat and setup and setup.rr_ok:
            verdict = "✅ *VALID SETUP* — meets Pinpoint criteria"
        elif pat and setup:
            verdict = "⚠️ *WATCHLIST* — pattern found but R:R below 5:1"
        elif pat:
            verdict = "⚠️ *WATCHLIST* — pattern found, no measured target"
        else:
            verdict = "❌ *NO SETUP* — no valid pattern detected"
        lines.append(verdict)
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        log.error("analyse_ticker(%s) error: %s", ticker, e)
        return f"❌ Error analysing `{ticker}`: {e}"


def compare_tickers(t1: str, t2: str) -> str:
    lines = [f"📊 *{t1} vs {t2}*\n"]
    for ticker in [t1, t2]:
        try:
            from pinpoint import ohlcv as ohlcv_mod
            from pinpoint import patterns as patterns_mod
            from pinpoint import entries as entries_mod
            from pinpoint import stage_trend as stage_mod
            from pinpoint.ohlcv import add_moving_averages

            result = ohlcv_mod.fetch_daily(ticker, use_cache=True)
            if result.empty:
                lines.append(f"*{ticker}:* No data\n"); continue
            df = add_moving_averages(result.df)
            last = df.iloc[-1]
            price = float(last["Close"])
            sma200 = float(last.get("SMA200", 0)) if "SMA200" in df.columns else 0
            sma50 = float(last.get("SMA50", 0)) if "SMA50" in df.columns else 0
            ema20 = float(last.get("EMA20", 0)) if "EMA20" in df.columns else 0
            stage = stage_mod.classify_from_sma(
                (price - ema20) / ema20 * 100 if ema20 else 0,
                (price - sma50) / sma50 * 100 if sma50 else 0,
                (price - sma200) / sma200 * 100 if sma200 else 0)
            pat = patterns_mod.best_pattern(df, require_measured=True)
            setup = entries_mod.compute_setup(pat.trigger, pat.support_low, pat.measured_target) if pat else None
            rr_str = (f"{setup.reward_risk:.1f}:1 {'✅' if setup and setup.rr_ok else '⚠️'}"
                      if setup else "n/a")
            lines.append(f"*{ticker}* ${price:.2f}\n{'✅' if stage.is_stage2 else '❌'} {stage.label}\n"
                         f"Pattern: {pat.label if pat else 'No pattern'}\nR:R: {rr_str}\n")
        except Exception as e:  # noqa: BLE001
            lines.append(f"*{ticker}:* Error — {e}\n")
    return "\n".join(lines)


def custom_position_size(ticker: str, entry: float, stop: float) -> str:
    account_size, risk_pct = _account_settings()
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return "❌ Stop must be below entry price."
    dollar_risk = account_size * (risk_pct / 100)
    shares = int(dollar_risk / risk_per_share)
    position_value = shares * entry
    target_3r = entry + risk_per_share * 3
    target_5r = entry + risk_per_share * 5
    try:
        from pinpoint.entries import liquidity_stop
        sr = liquidity_stop(stop)
        stop_display = f"${sr.stop:.2f} ({sr.cluster_kind} .89 rule)"
    except Exception:  # noqa: BLE001
        stop_display = f"${stop:.2f}"
    return (f"📐 *{ticker} Position Size*\n\n"
            f"Account: ${account_size:,.0f} · Risk: {risk_pct}%\n\n"
            f"Entry:    `${entry:.2f}`\nStop:     `{stop_display}`\n"
            f"Risk/sh:  `${risk_per_share:.2f}`\n$ at risk: `${dollar_risk:,.0f}`\n"
            f"*Shares:   {shares}*\nPosition: `${position_value:,.0f}`\n\n"
            f"3R target: `${target_3r:.2f}`\n5R target: `${target_5r:.2f}` ← PDF target\n\n"
            f"_Trail on 10 EMA daily close_")


def cmd_regime() -> str:
    try:
        from pinpoint import ohlcv as ohlcv_mod
        from pinpoint.ohlcv import add_moving_averages
        lines = ["📊 *Market Regime*\n"]
        for sym in ["SPY", "QQQ"]:
            r = ohlcv_mod.fetch_daily(sym, use_cache=True)
            if r.empty:
                continue
            df = add_moving_averages(r.df)
            last = df.iloc[-1]
            price = float(last["Close"])
            sma200 = float(last.get("SMA200", 0)) if "SMA200" in df.columns else 0
            ema20 = float(last.get("EMA20", 0)) if "EMA20" in df.columns else 0
            lines.append(f"*{sym}* ${price:.2f}\n  200 SMA: {'✅' if price > sma200 else '❌'} "
                         f"${sma200:.2f}\n  20 EMA:  {'✅' if price > ema20 else '❌'} ${ema20:.2f}")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"❌ Regime check failed: {e}"


def cmd_watchlist() -> str:
    try:
        from pinpoint import store
        cache = store.load_scan_cache()
        if not cache:
            return "❌ No scan data. Run scan first."
        focus = cache.lists.get("focus")
        if focus is None or len(focus) == 0:
            return "📋 No setups today."
        lines = [f"📋 *Today's Setups* — {cache.regime_state}\n"]
        for _, row in focus.head(5).iterrows():
            tier_emoji = "🔥" if row.get("tier") == "Elite" else "⚡"
            lines.append(f"{tier_emoji} *{row['ticker']}* RS {row.get('rs', 0):.0f} · "
                         f"Score {row.get('pinpoint_score', 0):.0f}\n   {row.get('pattern', '')} · "
                         f"E `${row.get('entry_trigger', 0):.2f}` X `${row.get('stop', 0):.2f}`")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"❌ Watchlist error: {e}"


def cmd_positions() -> str:
    try:
        import json
        from pinpoint import ohlcv as ohlcv_mod
        from pinpoint.ohlcv import add_moving_averages
        pos_path = "data/store/positions.json"
        if not os.path.exists(pos_path):
            return "📭 No open positions."
        with open(pos_path) as f:
            positions = json.load(f)
        open_pos = [p for p in positions if p.get("status") == "OPEN"]
        if not open_pos:
            return "📭 No open positions."
        lines = [f"📈 *Open Positions ({len(open_pos)})*\n"]
        for pos in open_pos:
            ticker = pos["ticker"]
            entry, stop, shares = pos.get("entry", 0), pos.get("stop", 0), pos.get("shares", 0)
            risk = entry - stop
            r = ohlcv_mod.fetch_daily(ticker, use_cache=True)
            if r.empty:
                lines.append(f"• *{ticker}* — no price data"); continue
            df = add_moving_averages(r.df)
            price = float(df["Close"].iloc[-1])
            ema10 = float(df["EMA10"].iloc[-1]) if "EMA10" in df.columns else 0
            current_rr = (price - entry) / risk if risk > 0 else 0
            pnl = (price - entry) * shares
            emoji = "✅" if current_rr >= 1 else "🟡" if current_rr >= 0 else "🔴"
            lines.append(f"{emoji} *{ticker}* ${price:.2f}\n   {'+' if current_rr >= 0 else ''}"
                         f"{current_rr:.1f}R · ${pnl:+,.0f} · {shares} sh\n"
                         f"   Entry ${entry:.2f} · Stop ${stop:.2f}\n"
                         f"   Trail: {f'10 EMA ${ema10:.2f}' if ema10 else ''}")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"❌ Positions error: {e}"


def cmd_help() -> str:
    return ("📍 *Pinpoint Bot Commands*\n\n"
            "`$AAPL` — Full analysis\n`$AAPL vs $NVDA` — Compare two stocks\n"
            "`$AAPL entry 295 stop 280` — Position size\n\n"
            "`/regime` — Market regime (SPY/QQQ)\n`/watchlist` — Today's top setups\n"
            "`/positions` — Open positions + P&L\n`/help` — This message\n\n"
            "_All analysis uses Friday's close on weekends._")


# ── Message parser ─────────────────────────────────────────
def parse_and_respond(text: str, message_id: int):
    text = text.strip()
    low = text.lower()
    if low in ("/help", "/start"):
        send(cmd_help(), reply_to=message_id); return
    if low == "/regime":
        typing(); send(cmd_regime(), reply_to=message_id); return
    if low == "/watchlist":
        typing(); send(cmd_watchlist(), reply_to=message_id); return
    if low == "/positions":
        typing(); send(cmd_positions(), reply_to=message_id); return

    vs = re.match(r"\$([A-Z]{1,5})\s+vs\s+\$([A-Z]{1,5})", text, re.IGNORECASE)
    if vs:
        typing(); send(compare_tickers(vs.group(1).upper(), vs.group(2).upper()), reply_to=message_id); return

    sz = re.match(r"\$([A-Z]{1,5})\s+entry\s+([\d.]+)\s+stop\s+([\d.]+)", text, re.IGNORECASE)
    if sz:
        typing()
        send(custom_position_size(sz.group(1).upper(), float(sz.group(2)), float(sz.group(3))),
             reply_to=message_id); return

    tk = re.match(r"\$([A-Z]{1,5})\b", text, re.IGNORECASE)
    if tk:
        t = tk.group(1).upper()
        typing()
        send(f"_Analysing {t}..._", reply_to=message_id)
        send(analyse_ticker(t), reply_to=message_id)
        return


# ── Main poll loop ─────────────────────────────────────────
def main():
    if not TOKEN or not CHAT_ID:
        print("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env")
        return
    print(f"\n📍 Pinpoint Telegram Bot running\n   Channel: {CHAT_ID}\n"
          "   Commands: $TICKER, $T vs $T, /regime, /watchlist, /positions\n"
          "   Press Ctrl+C to stop\n")
    offset = 0
    while True:
        try:
            for update in get_updates(offset):
                offset = update["update_id"] + 1
                msg = update.get("message") or update.get("channel_post")
                if not msg:
                    continue
                if str(msg.get("chat", {}).get("id", "")) != str(CHAT_ID):
                    continue
                text = (msg.get("text") or "").strip()
                if not text or not (text.startswith("$") or text.startswith("/")):
                    continue
                log.info("Command received: %s", text)
                try:
                    parse_and_respond(text, msg.get("message_id"))
                except Exception as e:  # noqa: BLE001
                    log.error("Handler error: %s", e)
                    send(f"❌ Error: {e}", reply_to=msg.get("message_id"))
        except KeyboardInterrupt:
            print("\nBot stopped."); break
        except Exception as e:  # noqa: BLE001
            log.error("Poll loop error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
