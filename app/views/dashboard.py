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
c.warning_banner(scan.get("warnings"))


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


# ---- Top 10 compact cards ----
st.markdown("<div class='pp-section'>Top 10 — Focus + Watch</div>", unsafe_allow_html=True)
if len(top10) == 0:
    st.markdown("<div class='pp-empty'>No ranked names.</div>", unsafe_allow_html=True)
for i, (_, row) in enumerate(top10.iterrows()):
    c.compact_card(row.to_dict(), detail_fn, key=f"card{i}")

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

# ---- Compact watchlist strip ----
st.markdown("<div class='pp-section'>Watchlist · at a glance</div>", unsafe_allow_html=True)
wl = store.load_watchlist()
if not wl:
    st.markdown("<div class='pp-empty'>No saved names yet — ★ Save from any card.</div>",
                unsafe_allow_html=True)
else:
    key = f"wlstrip::{','.join(wl)}::{'c' if CLOUD else 'l'}"
    if st.session_state.get("wlstrip_key") != key:
        ref, _ = c.reference_universe()
        with st.spinner("Grading watchlist…"):
            st.session_state["wlstrip"] = analyzer.analyze_picks(
                None if CLOUD else c.get_client(), wl, scan["regime"],
                reference_universe=ref, theme_ctx=theme_ctx,
                offline_universe=ref if CLOUD else None, cache_only=CLOUD)
        st.session_state["wlstrip_key"] = key
    c.watchlist_strip(st.session_state.get("wlstrip", []))

c.disclaimer_footer()
