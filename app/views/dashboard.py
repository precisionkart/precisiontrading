"""Dashboard view — Top-10 compact cards, sector treemap, earnings, watchlist strip."""

import json
import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as c
import dashboard_logic as dl
from pinpoint import store, analyzer
from pinpoint import themes as themes_mod
from pinpoint.config import CONFIG

CLOUD = c.cloud_mode()
scan = st.session_state.get("scan")

c.page_header("Today")
c.weekend_banner()
c.open_positions_panel()        # OPEN POSITIONS widget (hidden when none)
if not scan:
    if CLOUD:
        msg = "No published scan found (data/latest_scan.json)."
    else:
        nxt = c.next_scheduled_scan().strftime("%a %-d %b, 09:30")
        msg = (f"No scan today — next scheduled scan: {nxt} ET. "
               "Or hit the ↻ refresh in the sidebar to run one now.")
    st.markdown(f"<div class='pp-sub'>{msg}</div>", unsafe_allow_html=True)
    c.disclaimer_footer()
    st.stop()

theme_ctx = scan.get("theme_ctx") or themes_mod.context_from_list(scan.get("themes", []))
sector_filter = st.session_state.get("sector_filter")
# Stale-data banner: if the loaded scan isn't from today, say so plainly so old
# setups are never mistaken for fresh ones (the header dot already flags it).
_lbl, _col = c.refresh_status(scan)
if _col != "#00D964":
    nxt = c.next_scheduled_scan().strftime("%a %-d %b, 09:30")
    st.markdown(
        f"<div class='pp-stale'>⚠ Showing the last scan ({c._html.escape(_lbl)}) — "
        f"not from today. Hit ↻ in the sidebar to run a fresh one, or wait for the "
        f"next scheduled scan: {nxt} ET.</div>", unsafe_allow_html=True)
c.warning_banner(scan.get("warnings"))

focus = scan.get("focus")
ef_tickers = set()
if focus is not None and len(focus) and "earnings_flag_active" in focus.columns:
    ef_tickers = set(focus[focus["earnings_flag_active"] == True]["ticker"].astype(str))  # noqa: E712

# ---- Sector strength — compact pill row (replaces the treemap) ----
st.markdown("<div class='pp-section'>Sector strength</div>", unsafe_allow_html=True)
c.sector_pills(scan.get("themes", []))


def detail_fn(tk: str):
    cache = st.session_state.setdefault("detail_cache", {})
    key = f"{tk}:{'c' if CLOUD else 'l'}"
    if key not in cache:
        ref, _ = c.reference_universe()
        with st.spinner(f"Analyzing {tk}…"):
            res = analyzer.analyze_picks(
                None if CLOUD else c.get_client(), [tk], scan["regime"],
                reference_universe=ref, theme_ctx=theme_ctx,
                offline_universe=ref if CLOUD else None, cache_only=CLOUD)
        cache[key] = res[0] if res else None
    return cache[key]


# ---- Tiered output (Phase 10 step 6) ----
tiers = dl.build_tiers(scan.get("focus"), scan.get("targets"), sector_filter)
t1 = tiers[tiers["Tier"] == "Elite"]
t2 = tiers[tiers["Tier"] == "Good"]
t3 = tiers[tiers["Tier"] == "Watchlist"]
_tgt = scan.get("targets")
scanned = max(len(_tgt) if _tgt is not None else 0, len(tiers))  # ranked universe size
st.markdown("<div class='pp-section' style='margin-top:28px'>Setups</div>", unsafe_allow_html=True)
st.markdown(
    f"<div class='pp-tiercount'>Ranked {scanned} · {len(t1)} Elite · "
    f"{len(t2)} Good · {len(t3)} Watch</div>", unsafe_allow_html=True)

if len(tiers) == 0:
    st.markdown("<div class='pp-empty'>No names scored 50+.</div>", unsafe_allow_html=True)

_ci = 0
if len(t1):
    st.markdown("<div class='pp-section'>🔥 Elite (80-100)</div>", unsafe_allow_html=True)
    for _, row in t1.iterrows():
        c.compact_card(row.to_dict(), detail_fn, key=f"card{_ci}",
                       ef=str(row.get("Ticker")) in ef_tickers, tier=1); _ci += 1
if len(t2):
    st.markdown("<div class='pp-section'>⚡ Good (65-79)</div>", unsafe_allow_html=True)
    for _, row in t2.iterrows():
        c.compact_card(row.to_dict(), detail_fn, key=f"card{_ci}",
                       ef=str(row.get("Ticker")) in ef_tickers, tier=2); _ci += 1
if len(t3):
    st.markdown("<div class='pp-section'>•• Watchlist (50-64)</div>", unsafe_allow_html=True)
    _t3 = list(t3.iterrows())
    per_row = 6
    for _r0 in range(0, len(_t3), per_row):
        chunk = _t3[_r0:_r0 + per_row]
        cols = st.columns(per_row)
        for _ci3, (_, r) in enumerate(chunk):
            tkp = str(r.get("Ticker"))
            with cols[_ci3]:
                if st.button(f"{tkp} {r.get('Score'):.0f}", key=f"wp_{tkp}",
                             help=f"Analyse {tkp}", use_container_width=True):
                    st.session_state["mp_prefill"] = tkp
                    st.switch_page("views/my_picks.py")
c.scroll_to_card()   # smooth-scroll to a card opened from a deep-link

# ---- Earnings reactions — both directions (Phase 10 step 8) ----
universe_tks = set()
_t = scan.get("targets")
if _t is not None and len(_t) and "ticker" in _t.columns:
    universe_tks = set(_t["ticker"].astype(str))
st.markdown("<div class='pp-section'>Overnight Earnings Reactions</div>", unsafe_allow_html=True)
ecol1, ecol2 = st.columns(2)
with ecol1:
    c.earnings_panel(scan.get("earnings"), "📈 Gapping Up", "up", universe_tks)
with ecol2:
    c.earnings_panel(scan.get("earnings_down"), "📉 Gapping Down (AVOID)", "down", universe_tks)

# (Dashboard ends at Earnings — the watchlist lives on its own sidebar page.)
c.disclaimer_footer()
