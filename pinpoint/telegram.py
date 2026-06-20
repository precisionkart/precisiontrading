"""telegram.py — single place for all Pinpoint Telegram sends.

Every send is best-effort: a failure (or an unconfigured bot) logs and returns
False, never raising — so a scan/monitor run is never blocked by Telegram. All
times shown to the user are UK time (Europe/London). Configure via env:
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
"""

import os
import logging

import requests

try:
    import pytz
    UK_TZ = pytz.timezone("Europe/London")
except Exception:  # noqa: BLE001 — pytz optional; only used for display niceties
    UK_TZ = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass

log = logging.getLogger("pinpoint.telegram")

TELEGRAM_MAX = 4096


class TelegramBot:
    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        """Send a message. Never raises — always best-effort."""
        if not self.enabled:
            log.debug("Telegram not configured — skipping")
            return False
        if len(text) > TELEGRAM_MAX:                  # Telegram hard limit
            text = text[:TELEGRAM_MAX - 20] + "\n… (truncated)"
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=5)
            ok = resp.json().get("ok", False)
            if not ok:
                log.warning("Telegram send failed: %s", resp.text[:200])
            return ok
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram send failed: %s", e)
            return False

    # ── SCHEDULED MESSAGES ──────────────────────────────────────────
    def send_morning_brief(self, regime, stalking, watching, earnings_today,
                           open_positions, sectors):
        """8:00am UK — Morning Brief."""
        regime_emoji = {"BULL": "🟢", "NEUTRAL": "🟡", "BEAR": "🔴",
                        "RECOVERY": "🔵"}.get(str(regime).upper(), "⚪")
        lines = ["Good Morning Sweet Cheeks 🌤️\n", f"*Regime: {regime}* {regime_emoji}"]
        if sectors:
            lines.append(" · ".join(f"{s['name']} #{i+1}" for i, s in enumerate(sectors[:3])) + "\n")
        if stalking:
            lines.append("*STALK TODAY (set buy stops):*")
            for s in stalking[:5]:
                e = s.get("entry_trigger", s.get("entry", 0))
                lines.append(f"`{s['ticker']}` ${e:.2f} stop ${s['stop']:.2f} — "
                             f"{s['pattern']} RS {s['rs']:.0f}")
        else:
            lines.append("*STALK TODAY:* _None ready — be patient_")
        lines.append("")
        if watching:
            lines.append("*WATCHING (not ready):*")
            for w in watching[:5]:
                lines.append(f"`{w['ticker']}` — score {w['score']:.0f}, {w.get('reason', 'watching')}")
        lines.append("")
        if earnings_today:
            lines.append("*EARNINGS TODAY:*")
            for e in earnings_today[:3]:
                lines.append(f"`{e['ticker']}` reports {e.get('when', 'after close')} "
                             f"— avoid new positions in this name")
        n_open = len(open_positions)
        if n_open > 0:
            lines.append(f"\n*Open positions: {n_open}*")
            for p in open_positions:
                rr = p.get("current_rr", 0)
                emoji = "✅" if rr >= 1 else "🟡" if rr >= 0 else "🔴"
                lines.append(f"{emoji} `{p['ticker']}` {'+' if rr >= 0 else ''}{rr:.1f}R")
        else:
            lines.append("\n*Open positions: 0*")
        lines.append("\n_Market opens 2:30pm UK. Good luck._")
        return self.send("\n".join(lines))

    def send_market_open(self, pending, open_positions):
        """2:30pm UK -- Market Open ping."""
        import requests
        msg = "🔔 MARKET OPENNN!\n\nWe don't start dialing at 9:30 because our clients are already answering the phone. Three. Two. One. Let's f*ck!"
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": msg},
                timeout=10,
            )
            return resp.ok
        except Exception:
            return False

    def send_market_close(self, open_positions, closed_today):
        """9:00pm UK — Market Close summary."""
        lines = ["🔕 *Market Closed*\n"]
        if closed_today:
            day_r = sum(p.get("r_multiple", 0) for p in closed_today)
            emoji = "📈" if day_r >= 0 else "📉"
            lines.append(f"{emoji} *Today's P&L: {'+' if day_r >= 0 else ''}{day_r:.1f}R*\n")
        if open_positions:
            lines.append("*Open positions:*")
            for p in open_positions:
                rr = p.get("current_rr", 0)
                status = p.get("exit_signal", "holding")
                emoji = "✅" if rr >= 1 else "🟡" if rr >= 0 else "🔴"
                lines.append(f"{emoji} `{p['ticker']}` {'+' if rr >= 0 else ''}{rr:.1f}R — {status}")
        if closed_today:
            lines.append("\n*Closed today:*")
            for p in closed_today:
                rr = p.get("r_multiple", 0)
                emoji = "✅" if rr > 0 else "🔴"
                lines.append(f"{emoji} `{p['ticker']}` {'+' if rr >= 0 else ''}{rr:.1f}R "
                             f"— {p.get('exit_reason', '')}")
        lines.append("\n_EOD scan running at 9:30pm UK._")
        return self.send("\n".join(lines))

    def send_eod_scan(self, regime, new_setups, promoted, degraded,
                      open_positions, sectors):
        """9:30pm UK — EOD scan complete. New setups for tomorrow."""
        regime_emoji = {"BULL": "🟢", "NEUTRAL": "🟡", "BEAR": "🔴",
                        "RECOVERY": "🔵"}.get(str(regime).upper(), "⚪")
        lines = ["📍 *EOD Scan Complete*\n", f"*Regime: {regime}* {regime_emoji}"]
        if sectors:
            lines.append(f"{sectors[0]['name']} leading\n")
        if new_setups:
            lines.append("*NEW FOR TOMORROW:*")
            for s in new_setups[:3]:
                e = s.get("entry_trigger", s.get("entry", 0))
                lines.append(f"`{s['ticker']}` — {s['pattern']} | Score {s['score']:.0f} | "
                             f"E ${e:.2f} R:R {s['rr']:.1f}:1")
        else:
            lines.append("*NEW:* _No new setups tonight_")
        if promoted:
            lines.append("\n*PROMOTED TO STALK:*")
            for p in promoted:
                e = p.get("entry_trigger", p.get("entry", 0))
                lines.append(f"⭐ `{p['ticker']}` score {p['old_score']:.0f}→{p['new_score']:.0f}"
                             f" — set buy stop ${e:.2f}")
        if degraded:
            lines.append("\n*DEGRADED:*")
            for d in degraded:
                lines.append(f"⚠️ `{d['ticker']}` score {d['old_score']:.0f}→{d['new_score']:.0f}"
                             f" — {d.get('reason', 'setup invalidated')}")
        lines.append("\n_Morning brief at 8am UK._")
        return self.send("\n".join(lines))

    def send_weekly_wrap(self, week_r, trades, weekend_watchlist):
        """Friday 9:30pm UK — Weekly wrap."""
        emoji = "📈" if week_r >= 0 else "📉"
        lines = ["📍 *Weekly Wrap*\n",
                 f"{emoji} *Week P&L: {'+' if week_r >= 0 else ''}{week_r:.1f}R*\n"]
        if trades:
            lines.append("*Trades this week:*")
            for t in trades:
                rr = t.get("r_multiple", 0)
                status = "open" if t.get("status") == "OPEN" else t.get("exit_reason", "closed")
                emoji2 = "✅" if rr > 0 else "🔴"
                lines.append(f"{emoji2} `{t['ticker']}` {'+' if rr >= 0 else ''}{rr:.1f}R — {status}")
        if weekend_watchlist:
            lines.append("\n*Weekend watchlist (Monday):*")
            lines.append(" · ".join(f"`{t}`" for t in weekend_watchlist[:6]))
        lines.append("\n_Scan runs Monday 8am UK brief._\nHave a great weekend 🙌")
        return self.send("\n".join(lines))

    # ── POSITION ALERTS (fire immediately when triggered) ───────────
    def send_entry_triggered(self, ticker, entry, stop, target_3r, target_5r,
                             pattern, shares):
        """Fires when buy stop price is hit."""
        lines = ["✅ *BUY STOP TRIGGERED*\n", f"`{ticker}` filled ~${entry:.2f}\n",
                 f"Pattern: {pattern}", f"Stop:    ${stop:.2f}",
                 f"3R:      ${target_3r:.2f} → trim 20% here",
                 f"5R:      ${target_5r:.2f} → trim 30% here",
                 f"Shares:  {shares}\n", "_Position is now LIVE._",
                 "_Trail on 10 EMA daily close._"]
        return self.send("\n".join(lines))

    def send_position_alert(self, alert: dict):
        """Fires for HARD_STOP / EMA10_TRAIL / TRIM_SIGNAL / PARABOLIC / NEAR_STOP
        (CRITICAL and HIGH urgency)."""
        type_formats = {
            "HARD_STOP": ("⛔", "HARD STOP HIT", "Exit immediately in IBKR"),
            "EMA10_TRAIL": ("⚠️", "10 EMA BROKEN", "PDF: close below 10 EMA = exit signal"),
            "EMA20_TRAIL": ("⚠️", "20 EMA BROKEN", "Review position — consider exit"),
            "TRIM_SIGNAL": ("📈", "TRIM SIGNAL", "Sell 20% in IBKR now"),
            "PARABOLIC": ("🚀", "PARABOLIC MOVE", "Trim aggressively — switch to 5 EMA trail"),
            "NEAR_STOP": ("🔴", "NEAR STOP", "Monitor closely — stop within 2%"),
        }
        emoji, title, default_action = type_formats.get(
            alert.get("type"), ("📊", str(alert.get("type", "ALERT")), "Review position"))
        rr = alert.get("current_rr", 0)
        lines = [f"{emoji} *{title}*\n",
                 f"`{alert['ticker']}` @ ${alert.get('price', 0):.2f}",
                 alert.get("message", ""),
                 f"Current R: {'+' if rr >= 0 else ''}{rr:.1f}R\n",
                 f"→ _{alert.get('action', default_action)}_"]
        if alert.get("note"):
            lines.append(f"\n_{alert['note']}_")
        return self.send("\n".join(lines))

    def send_watchlist_promoted(self, ticker, old_score, new_score, entry, stop,
                                rr, pattern, rs):
        """Fires when a watchlist name is promoted to Tier 1 (score 75+)."""
        lines = [f"⭐ *SETUP READY — {ticker}*\n",
                 f"Score jumped {old_score:.0f} → {new_score:.0f}\n",
                 f"Pattern: {pattern}", f"Entry:   ${entry:.2f}", f"Stop:    ${stop:.2f}",
                 f"R:R:     {rr:.1f}:1", f"RS:      {rs:.0f}\n", "_→ Set buy stop in IBKR now_"]
        return self.send("\n".join(lines))

    def send_watchlist_degraded(self, ticker, old_score, new_score, reason):
        """Fires when a stalking name degrades below 60."""
        lines = [f"⚠️ *SETUP DEGRADED — {ticker}*\n",
                 f"Score dropped {old_score:.0f} → {new_score:.0f}", f"Reason: {reason}\n",
                 "_→ Cancel buy stop in IBKR_", "_→ Remove from watchlist_"]
        return self.send("\n".join(lines))

    def send_scan_complete(self, n_total, n_elite, n_good, top_ticker, top_score, regime):
        """Quick ping when scan finishes — tier breakdown across the ranked list."""
        return self.send(
            f"✅ *Scan complete*\n"
            f"Regime: {regime}\n\n"
            f"🔥 Elite (80+): {n_elite}\n"
            f"⚡ Good (65-79): {n_good}\n"
            f"Total ranked: {n_total}\n\n"
            f"Top: `{top_ticker}` score {top_score:.0f}\n"
            f"_Full brief at 8am UK_")


# ── Singleton ──────────────────────────────────────────────────────
_bot = None


def get_bot() -> TelegramBot:
    global _bot
    if _bot is None:
        _bot = TelegramBot()
    return _bot
