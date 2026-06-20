#!/usr/bin/env python3
"""
Pinpoint Trading — Full Day Demo
Sends all daily alerts to Telegram instantly to demonstrate
the system to someone.

Run: python demo_day.py
"""

import os
import time
from dotenv import load_dotenv
load_dotenv()

import sys
sys.path.insert(0, os.path.dirname(__file__))

from pinpoint.telegram import get_bot

bot = get_bot()


def send(text: str):
    """Send message with tiny delay so they don't overlap."""
    bot.send(text)
    time.sleep(1.5)


def run_demo():
    print("🎬 Sending full day demo to Precision Trading channel...")
    print("Check your Telegram channel now.\n")

    # ── 1. MORNING BRIEF ──────────────────────────────────────
    print("1. Morning Brief...")
    send("""Good Morning Sweet Cheeks 🌤️

*Regime: BULL* 🟢
Semiconductors #1 · Technology #2 · Solar #3

*STALK TODAY (set buy stops):*
`DOCN` $187.51 stop $168.89 — High & tight flag RS 95
`MXL` $94.81 stop $81.89 — Bullish falling wedge RS 98

*WATCHING (not ready):*
`APLD` — score 69, needs more time
`TER` — score 65, watching
`SMCI` — score 64, watching

*EARNINGS TODAY:*
`NVDA` reports after close — avoid new positions

*Open positions: 0*

_Market opens 2:30pm UK. Good luck._""")

    # ── 2. PRE-MARKET GAPS ────────────────────────────────────
    print("2. Pre-market earnings gaps...")
    send("""⚡ *Pre-Market Earnings Gaps*

📈 *Gapping UP (reaction > numbers):*
`CRWD` +8.2% — beat EPS by 23%, raised guidance
`APP` +5.1% — record revenue quarter

📉 *Gapping DOWN (avoid):*
`SNOW` -4.8% — missed revenue estimates

_Watch CRWD for an earnings flag setup forming._""")

    # ── 3. MARKET OPEN ────────────────────────────────────────
    print("3. Market Open bell...")
    send("""🔔 *Market Open*

*Buy stops active: 2*
`DOCN` $187.51 · `MXL` $94.81

*Positions live: 0*

_Monitor running. Will alert on any triggers._""")

    # ── 4. ENTRY TRIGGERED ────────────────────────────────────
    print("4. Entry triggered — DOCN...")
    send("""✅ *BUY STOP TRIGGERED*

`DOCN` filled ~$187.51

Pattern: High & tight flag
Stop:    $168.89
3R:      $243.75 → trim 20% here
5R:      $281.01 → trim 30% here

_Position is now LIVE._
_Trail on 10 EMA daily close._""")

    # ── 5. WATCHLIST PROMOTED ─────────────────────────────────
    print("5. Watchlist promoted — SMCI...")
    send("""⭐ *SETUP READY — SMCI*

Score jumped 64 → 82

Pattern: Flat base breakout
Entry:   $45.20
Stop:    $41.89
R:R:     6.8:1
RS:      96

_→ Set buy stop in IBKR now_""")

    # ── 6. SECOND ENTRY ───────────────────────────────────────
    print("6. Entry triggered — MXL...")
    send("""✅ *BUY STOP TRIGGERED*

`MXL` filled ~$94.81

Pattern: Bullish falling wedge
Stop:    $81.89
3R:      $133.57 → trim 20% here
5R:      $159.41 → trim 30% here

_Position is now LIVE._
_Trail on 10 EMA daily close._""")

    # ── 7. TRIM SIGNAL ────────────────────────────────────────
    print("7. Trim signal — DOCN at 3R...")
    send("""📈 *TRIM SIGNAL*

`DOCN` @ $243.75

Extended 8.1% above 5 EMA at 3.0R
Current R: +3.0R

→ _Sell 20% in IBKR now_""")

    # ── 8. PARABOLIC ──────────────────────────────────────────
    print("8. Parabolic move — DOCN...")
    send("""🚀 *PARABOLIC MOVE*

`DOCN` @ $265.40

+22.3% above 5 EMA
Current R: +4.2R

→ _Trim aggressively — switch to 5 EMA trail_

_PDF: >20% above 5 EMA = trim + tighten trail_""")

    # ── 9. NEAR STOP WARNING ──────────────────────────────────
    print("9. Near stop — MXL...")
    send("""🔴 *NEAR STOP*

`MXL` @ $83.20

Within 2% of stop $81.89
Current R: +0.1R

→ _Monitor closely — be ready to exit_""")

    # ── 10. 10 EMA TRAIL BROKEN ───────────────────────────────
    print("10. EMA trail broken — MXL...")
    send("""⚠️ *10 EMA BROKEN*

`MXL` @ $90.80

Closed below 10 EMA ($91.20)
Current R: +0.8R

→ _Review exit in IBKR_

_PDF: close below 10 EMA = exit signal_""")

    # ── 11. MARKET CLOSE ──────────────────────────────────────
    print("11. Market Close bell...")
    send("""🔕 *Market Closed*

📈 *Today's P&L: +3.2R*

*Open positions:*
✅ `DOCN` +4.2R — parabolic, trailing 5 EMA
🟡 `MXL`  +0.8R — exited at 10 EMA break

*Closed today:*
✅ `MXL` +0.8R — 10 EMA trail exit

_EOD scan running at 9:30pm UK._""")

    # ── 12. WATCHLIST DEGRADED ────────────────────────────────
    print("12. Watchlist degraded — TER...")
    send("""⚠️ *SETUP DEGRADED — TER*

Score dropped 65 → 38
Reason: Pattern invalidated — broke below 20 EMA

_→ Cancel buy stop in IBKR_
_→ Remove from watchlist_""")

    # ── 13. EOD SCAN COMPLETE ─────────────────────────────────
    print("13. EOD Scan complete...")
    send("""📍 *EOD Scan Complete*

*Regime: BULL* 🟢
Semiconductors leading

*NEW FOR TOMORROW:*
`NVDA` — Earnings flag | Score 91 | E $892 R:R 8.1:1
`APP` — Inside day | Score 78 | E $312 R:R 5.9:1
`CRWD` — Earnings flag | Score 88 | E $425 R:R 7.2:1

*PROMOTED TO STALK:*
⭐ `NVDA` score 71→91 — set buy stop $892

*Open positions:*
✅ `DOCN` +4.2R — trailing 5 EMA

_Morning brief at 8am UK._""")

    # ── 14. SCAN COMPLETE PING ────────────────────────────────
    print("14. Scan complete ping...")
    send("""✅ *Scan complete*
Regime: BULL

🔥 Elite (80+): 3
⚡ Good (65-79): 2
Total ranked: 37

Top: `NVDA` score 91
_Full brief at 8am UK_""")

    # ── 15. TELEGRAM BOT DEMO ─────────────────────────────────
    print("15. Bot demo message...")
    send("""💬 *Telegram Bot Active*

Type any of these in the channel:

`$NVDA` — Full breakdown
`$AAPL vs $NVDA` — Compare
`$DOCN entry 187 stop 168` — Levels
`/regime` — Market regime
`/watchlist` — Today's setups
`/positions` — Open positions
`/help` — All commands

_Try it now 👆_""")

    print("\n✅ Demo complete! All 15 messages sent to Precision Trading channel.")
    print("The full day flow has been demonstrated.")


if __name__ == "__main__":
    run_demo()
