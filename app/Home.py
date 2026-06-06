"""Morning Dashboard (Phase 7.5 layout).

Top to bottom: header, the Top-10 ranked table (Focus + Watch), a drill-down
detail view that appears when a row is clicked (interactive Plotly charts,
color-coded pills, prominent RS badge, template technical analysis), the Sector
Strength widget (click a sector to filter the Top 10), overnight earnings, and
the watchlist. Works live (local) and read-only (cloud, from latest_scan.json).
"""

import json
import os

import pandas as pd
import streamlit as st

import common as c
import dashboard_logic as dl
from pinpoint import store, analyzer, themes as themes_mod
from pinpoint.config import CONFIG

c.bootstrap("Morning Dashboard")
CLOUD = c.cloud_mode()


def _load_local_cache():
    cache = store.load_scan_cache()
    if cache is None or not cache.is_today:
        return None
    return {"regime": c.RegimeView(cache.regime_state, cache.regime_rationale),
            "theme_ctx": None, "themes": cache.themes,
            "focus": cache.lists.get("focus", pd.DataFrame()),
            "targets": cache.lists.get("targets", pd.DataFrame()),
            "earnings": cache.lists.get("earnings", pd.DataFrame()),
            "ipo": cache.lists.get("ipo", pd.DataFrame()),
            "as_of": cache.as_of, "warnings": []}


if "scan" not in st.session_state:
    st.session_state["scan"] = c.load_published() if CLOUD else _load_local_cache()
scan = st.session_state["scan"]

# ---- header + refresh ----
hcol1, hcol2 = st.columns([6, 1])
with hcol1:
    c.header("Morning Dashboard", scan["as_of"] if scan else "—")
with hcol2:
    if st.button("Re-pull" if CLOUD else "Refresh", use_container_width=True):
        st.session_state["scan"] = c.load_published() if CLOUD else c.full_scan()
        for k in ("watchlist_graded_key", "detail_key", "selected_ticker", "sector_filter"):
            st.session_state.pop(k, None)
        st.rerun()

if not scan:
    msg = ("No published scan found (data/latest_scan.json) — check the scheduled job."
           if CLOUD else
           "No scan yet today. Click <b>Refresh</b> to run a live scan (about a minute).")
    st.markdown(f"<div class='pp-empty'>{msg}</div>", unsafe_allow_html=True)
    c.disclaimer_footer()
    st.stop()

c.regime_line(scan["regime"].state, scan["regime"].rationale)
c.warning_banner(scan["warnings"])

# theme context for the detail view (rebuilt from rankings when no live pull)
theme_ctx = scan.get("theme_ctx") or themes_mod.context_from_list(scan.get("themes", []))

# ---- Top 10 ----
sector_filter = st.session_state.get("sector_filter")
label = "Top 10 — Focus + Watch"
if sector_filter:
    label += f"  ·  filtered: {sector_filter}"
st.markdown(f"<div class='pp-section'>{label}</div>", unsafe_allow_html=True)
if sector_filter and st.button("Clear filter", key="clearflt"):
    st.session_state.pop("sector_filter", None)
    st.rerun()

top10 = dl.build_top10(scan.get("focus"), scan.get("targets"), sector_filter)
selected = c.render_top10(top10, key="top10")
if selected:
    st.session_state["selected_ticker"] = selected

# ---- Detail (only when a row is selected) ----
tk = st.session_state.get("selected_ticker")
if tk:
    st.markdown("<div class='pp-section'>Detail</div>", unsafe_allow_html=True)
    detail_key = f"detail::{tk}::{'c' if CLOUD else 'l'}"
    if st.session_state.get("detail_key") != detail_key:
        ref_df, _ = c.reference_universe()
        with st.spinner(f"Analyzing {tk}..."):
            res = analyzer.analyze_picks(
                None if CLOUD else c.get_client(), [tk], scan["regime"],
                reference_universe=ref_df, theme_ctx=theme_ctx,
                offline_universe=ref_df if CLOUD else None, cache_only=CLOUD)
        st.session_state["detail_pr"] = res[0] if res else None
        st.session_state["detail_key"] = detail_key
    pr = st.session_state.get("detail_pr")
    if pr is not None:
        c.render_detail(pr)

# ---- Sector Strength ----
st.markdown("<div class='pp-section'>Sector Strength Today</div>", unsafe_allow_html=True)
hist_path = os.path.join(CONFIG.paths.data_dir, "theme_history.json")
history = {}
if os.path.exists(hist_path):
    try:
        history = json.load(open(hist_path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        history = {}
sector_rows = dl.sector_strength_rows(scan.get("themes", []), dl.prior_theme_scores(history))
clicked_sector = c.render_sector_strength(sector_rows, key="sector")
if clicked_sector and clicked_sector != sector_filter:
    st.session_state["sector_filter"] = clicked_sector
    st.rerun()

# ---- Earnings ----
st.markdown("<div class='pp-section'>Overnight Earnings Reactions</div>", unsafe_allow_html=True)
earn = scan.get("earnings")
if earn is None or len(earn) == 0:
    st.markdown("<div class='pp-empty'>No gapped-up earnings reactions.</div>",
                unsafe_allow_html=True)
else:
    cols = [x for x in ["ticker", "sector", "price", "gap", "rel_volume", "rs"] if x in earn.columns]
    styled = earn[cols].style.background_gradient(subset=[x for x in ["rs"] if x in cols],
                                                  cmap="Greens")
    st.dataframe(styled, use_container_width=True, hide_index=True)

# ---- Watchlist (supporting role) ----
st.markdown("<div class='pp-section'>My Watchlist</div>", unsafe_allow_html=True)
wl = store.load_watchlist()
if not wl:
    st.markdown("<div class='pp-empty'>Your watchlist is empty. Add tickers from the "
                "<b>My Picks</b> page and they'll be re-graded here each morning.</div>",
                unsafe_allow_html=True)
else:
    key = f"watchlist_graded::{','.join(wl)}::{'cloud' if CLOUD else 'live'}"
    if st.session_state.get("watchlist_graded_key") != key:
        ref_df, _ = c.reference_universe()
        with st.spinner("Re-grading your watchlist..."):
            st.session_state["watchlist_graded"] = analyzer.analyze_picks(
                None if CLOUD else c.get_client(), wl, scan["regime"],
                reference_universe=ref_df, theme_ctx=theme_ctx,
                offline_universe=ref_df if CLOUD else None, cache_only=CLOUD)
        st.session_state["watchlist_graded_key"] = key
    for pr in st.session_state.get("watchlist_graded", []):
        c.pick_card(pr)
        if st.button(f"Remove {pr.ticker}", key=f"rm_{pr.ticker}"):
            store.remove_from_watchlist(pr.ticker)
            st.session_state.pop("watchlist_graded_key", None)
            st.rerun()

c.disclaimer_footer()
