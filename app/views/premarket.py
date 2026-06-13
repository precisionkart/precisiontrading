"""Pre-Market Briefing — a morning at-a-glance (Phase 10 step 9).

Top: index levels (SPY/QQQ/IWM/DIA, overnight change, color-coded). Middle:
earnings gaps both directions. Bottom: top setups by tier. Index levels come
from the published snapshot on cloud, or a live (cached) fetch locally.
"""

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as c
import dashboard_logic as dl

CLOUD = c.cloud_mode()
scan = st.session_state.get("scan")

c.page_header("Pre-Market Briefing")
if not scan:
    st.markdown("<div class='pp-sub'>No scan loaded.</div>", unsafe_allow_html=True)
    c.disclaimer_footer()
    st.stop()

mode = scan.get("as_of_mode", "full")
st.markdown(f"<div class='pp-sub'>As of {c._html.escape(scan.get('as_of') or '—')} "
            f"· {('pre-market refresh' if mode == 'premarket' else 'evening full scan')}</div>",
            unsafe_allow_html=True)

# ---- Index levels ----
levels = scan.get("index_levels")
if not levels and not CLOUD:
    from pinpoint import pipeline
    with st.spinner("Fetching index levels…"):
        levels = pipeline.fetch_index_levels()
st.markdown("<div class='pp-section'>Updated Levels</div>", unsafe_allow_html=True)
if not levels:
    st.markdown("<div class='pp-empty'>Index levels unavailable.</div>", unsafe_allow_html=True)
else:
    cards = []
    for sym in ("SPY", "QQQ", "IWM", "DIA"):
        lv = levels.get(sym)
        if not lv:
            continue
        chg = lv.get("change_pct")
        up = isinstance(chg, (int, float)) and chg >= 0
        dot = "#00D964" if up else "#FF3366"
        ctxt = (f"{'+' if up else ''}{chg:.2f}%" if isinstance(chg, (int, float)) and chg == chg else "—")
        cards.append(
            f"<div class='pp-lvl'><span class='dot' style='background:{dot}'></span>"
            f"<span class='sym'>{sym}</span>"
            f"<span class='px'>${lv.get('price'):,.2f}</span>"
            f"<span class='chg' style='color:{dot}'>{ctxt}</span></div>")
    st.markdown("<div class='pp-lvls'>" + "".join(cards) + "</div>", unsafe_allow_html=True)

# regime exposure suggestion
_rstate = scan["regime"].state if scan.get("regime") else "neutral"
st.markdown(
    f"<div class='pp-exposure'>📊 Suggested portfolio exposure (regime "
    f"<b>{_rstate.upper()}</b>): <b>{c.regime_exposure(_rstate)}</b> "
    f"<span class='note'>— a suggestion, not a prescription.</span></div>",
    unsafe_allow_html=True)

# ---- Earnings gaps both directions ----
universe_tks = set()
_t = scan.get("targets")
if _t is not None and len(_t) and "ticker" in _t.columns:
    universe_tks = set(_t["ticker"].astype(str))
st.markdown("<div class='pp-section'>Earnings Gaps</div>", unsafe_allow_html=True)
gc1, gc2 = st.columns(2)
with gc1:
    c.earnings_panel(scan.get("earnings"), "📈 Gapping Up", "up", universe_tks)
with gc2:
    c.earnings_panel(scan.get("earnings_down"), "📉 Gapping Down (AVOID)", "down", universe_tks)

# ---- Top setups by tier ----
st.markdown("<div class='pp-section'>Top Setups by Tier</div>", unsafe_allow_html=True)
tiers = dl.build_tiers(scan.get("focus"), scan.get("targets"))
t1, t2, t3 = (tiers[tiers["Tier"] == k] for k in ("Elite", "Good", "Watchlist"))
st.markdown(
    f"<div class='pp-tiercount'>🔥 Tier 1: {len(t1)} · ⚡ Tier 2: {len(t2)} · "
    f"👀 Tier 3: {len(t3)}</div>", unsafe_allow_html=True)
for label, grp in (("🔥 Tier 1 — Elite", t1), ("⚡ Tier 2 — Good", t2), ("👀 Tier 3 — Watchlist", t3)):
    if not len(grp):
        continue
    pills = "".join(
        f"<span class='pp-tierpill'>{c._html.escape(str(r.get('Ticker')))}"
        f"<b>{r.get('Score'):.0f}</b></span>" for _, r in grp.iterrows())
    st.markdown(f"<div class='pp-section' style='font-size:12px'>{label}</div>"
                f"<div class='pp-tierpills'>{pills}</div>", unsafe_allow_html=True)

c.disclaimer_footer()
