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
top10 = dl.build_top10(scan.get("focus"), scan.get("targets"), sector_filter)
n_hot = sum(1 for t in scan.get("themes", []) if t.get("rank") and t["rank"] <= max(1, round(0.3 * len(scan.get("themes", [])))))
sub = f"{len(top10)} names · {n_hot} sectors hot"
if sector_filter:
    sub += f" · filtered: {sector_filter}"
st.markdown(f"<div class='pp-sub'>{sub}</div>", unsafe_allow_html=True)
_rstate = scan["regime"].state if scan.get("regime") else "neutral"
st.markdown(
    f"<div class='pp-exposure'>📊 Suggested portfolio exposure (regime "
    f"<b>{_rstate.upper()}</b>): <b>{c.regime_exposure(_rstate)}</b> "
    f"<span class='note'>— a suggestion, not a prescription; you size the trade.</span></div>",
    unsafe_allow_html=True)
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

# ---- Top 3 Podium — Tier 1/2 (>=65) qualifying, enriched setups ONLY ----
focus = scan.get("focus")
ef_tickers = set()
if focus is not None and len(focus) and "earnings_flag_active" in focus.columns:
    ef_tickers = set(focus[focus["earnings_flag_active"] == True]["ticker"].astype(str))  # noqa: E712
podium_rows = dl.build_podium(focus)
if podium_rows:
    st.markdown("<div class='pp-section'>Top 3 — best setups today</div>", unsafe_allow_html=True)
    c.render_podium(podium_rows)              # pads to 3 with "sitting in cash" cards
else:
    # Zero qualifying setups: hide the podium entirely (no fake "best setup").
    st.markdown("<div class='pp-cash-note'>No Pinpoint A+ setups today — "
                "wait for tomorrow's scan.</div>", unsafe_allow_html=True)


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
st.markdown(
    f"<div class='pp-tiercount'>Ranked {scanned} · 🔥 Tier 1: {len(t1)} · "
    f"⚡ Tier 2: {len(t2)} · 👀 Tier 3: {len(t3)}</div>", unsafe_allow_html=True)

if len(tiers) == 0:
    st.markdown("<div class='pp-empty'>No names scored 50+.</div>", unsafe_allow_html=True)

_ci = 0
if len(t1):
    st.markdown("<div class='pp-section'>🔥 Tier 1 — Elite (80-100)</div>", unsafe_allow_html=True)
    for _, row in t1.iterrows():
        c.compact_card(row.to_dict(), detail_fn, key=f"card{_ci}",
                       ef=str(row.get("Ticker")) in ef_tickers); _ci += 1
if len(t2):
    st.markdown("<div class='pp-section'>⚡ Tier 2 — Good Setups (65-79)</div>", unsafe_allow_html=True)
    for _, row in t2.iterrows():
        c.compact_card(row.to_dict(), detail_fn, key=f"card{_ci}",
                       ef=str(row.get("Ticker")) in ef_tickers); _ci += 1
if len(t3):
    st.markdown("<div class='pp-section'>👀 Tier 3 — Watchlist (50-64)</div>", unsafe_allow_html=True)
    pills = "".join(
        f"<span class='pp-tierpill'>{c._html.escape(str(r.get('Ticker')))}"
        f"<b>{r.get('Score'):.0f}</b></span>"
        for _, r in t3.iterrows())
    st.markdown(f"<div class='pp-tierpills'>{pills}</div>", unsafe_allow_html=True)
c.scroll_to_card()   # smooth-scroll to a card opened from the podium

# ---- Sector / Stocks heatmap (toggleable) ----
hcol1, hcol2 = st.columns([3, 1.4], vertical_alignment="center")
with hcol1:
    st.markdown("<div class='pp-section'>Sector Heatmap</div>", unsafe_allow_html=True)
with hcol2:
    hmview = st.radio("Heatmap view", ["Industries", "Stocks"], horizontal=True,
                      key="hmview", label_visibility="collapsed")

hist = {}
hp = os.path.join(CONFIG.paths.data_dir, "theme_history.json")
if os.path.exists(hp):
    try:
        hist = json.load(open(hp, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        hist = {}
tm_rows = dl.treemap_data(scan.get("themes", []), top10, dl.prior_theme_scores(hist))
if hmview == "Stocks":
    c.render_stocks_heatmap(dl.stocks_heatmap(scan.get("targets"), sector_filter))
else:
    c.render_treemap(tm_rows)
# reliable selectbox filter alongside the (visual) heatmap — drives both views
opts = ["All sectors"] + [r["label"] for r in tm_rows]
cur = sector_filter if sector_filter in opts else "All sectors"
sel = st.selectbox("Filter by sector", opts,
                   index=opts.index(cur), key="sectsel", label_visibility="collapsed")
new_filter = None if sel == "All sectors" else sel
if (new_filter or None) != (sector_filter or None):
    st.session_state["sector_filter"] = new_filter
    st.rerun()

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
