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

st.markdown("<div class='pp-h1'>Today</div>", unsafe_allow_html=True)
if not scan:
    msg = ("No published scan found (data/latest_scan.json)."
           if CLOUD else "No scan yet today — hit the ↻ refresh in the sidebar.")
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
c.warning_banner(scan.get("warnings"))

# ---- Top 3 Podium (Focus-source only; hidden entirely when 0 Focus) ----
focus = scan.get("focus")
ef_tickers = set()
if focus is not None and len(focus) and "earnings_flag_active" in focus.columns:
    ef_tickers = set(focus[focus["earnings_flag_active"] == True]["ticker"].astype(str))  # noqa: E712
focus_rows = []
if focus is not None and len(focus):
    f = focus.sort_values(["earnings_flag_active", "pinpoint_score"], ascending=False) \
        if "earnings_flag_active" in focus.columns else focus.sort_values("pinpoint_score", ascending=False)
    for _, r in f.head(3).iterrows():
        focus_rows.append({"ticker": r.get("ticker"), "sector": r.get("sector"),
                           "score": r.get("pinpoint_score"), "rs": r.get("rs"),
                           "pattern": r.get("pattern"), "entry": r.get("entry_trigger"),
                           "stop": r.get("stop"), "target": r.get("measured_target"),
                           "reward_risk": r.get("reward_risk"),
                           "earnings_flag": str(r.get("ticker")) in ef_tickers})
if focus_rows:
    st.markdown("<div class='pp-section'>Top 3 — best setups today</div>", unsafe_allow_html=True)
    c.render_podium(focus_rows)
else:
    st.markdown("<div class='pp-cash-note'>No Pinpoint A+ setups today — sitting in cash "
                "is a position.</div>", unsafe_allow_html=True)


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

# ---- Sector treemap ----
st.markdown("<div class='pp-section'>Sector Heatmap</div>", unsafe_allow_html=True)
hist = {}
hp = os.path.join(CONFIG.paths.data_dir, "theme_history.json")
if os.path.exists(hp):
    try:
        hist = json.load(open(hp, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        hist = {}
tm_rows = dl.treemap_data(scan.get("themes", []), top10, dl.prior_theme_scores(hist))
clicked = c.render_treemap(tm_rows)
# reliable selectbox filter alongside the (visual) treemap
opts = ["All sectors"] + [r["label"] for r in tm_rows]
cur = sector_filter if sector_filter in opts else "All sectors"
sel = st.selectbox("Filter Top 10 by sector", opts,
                   index=opts.index(cur), key="sectsel", label_visibility="collapsed")
new_filter = clicked or (None if sel == "All sectors" else sel)
if (new_filter or None) != (sector_filter or None):
    st.session_state["sector_filter"] = new_filter
    st.rerun()

# ---- Earnings (compact rows) ----
st.markdown("<div class='pp-section'>Overnight Earnings Reactions</div>", unsafe_allow_html=True)
earn = scan.get("earnings")
if earn is None or len(earn) == 0:
    st.markdown("<div class='pp-empty'>No gapped-up earnings reactions.</div>", unsafe_allow_html=True)
else:
    rows_html = []
    for _, r in earn.iterrows():
        gap = r.get("gap")
        gtxt = f"+{gap:.1f}%" if isinstance(gap, (int, float)) and gap == gap else "—"
        rows_html.append(
            f"<div class='pp-row'><span class='tk'>{r.get('ticker','')}</span>"
            f"<span class='px'>${r.get('price'):,.2f}</span>"
            f"<span class='score' style='color:#00D964;width:70px'>{gtxt}</span>"
            f"{c.rs_chip_html(r.get('rs'))}"
            f"<span class='pat'>earnings flag — watch for the light-volume flag</span></div>")
    st.markdown("<div style='display:flex;flex-direction:column;gap:6px'>"
                + "".join(rows_html) + "</div>", unsafe_allow_html=True)

# (Dashboard ends at Earnings — the watchlist lives on its own sidebar page.)
c.disclaimer_footer()
