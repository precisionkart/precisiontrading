"""Morning Dashboard — the landing page (spec Section 7B).

Three stacked sections: today's Focus picks, overnight earnings reactions, and
the saved watchlist re-graded fresh. Renders instantly from today's cached scan;
the Refresh button runs a live scan with spinners and partial-result handling.
"""

import pandas as pd
import streamlit as st

import common as c
from pinpoint import store, analyzer

c.bootstrap("Morning Dashboard")


def _load_from_cache():
    cache = store.load_scan_cache()
    if cache is None or not cache.is_today:
        return None
    return {"regime": c.RegimeView(cache.regime_state, cache.regime_rationale),
            "theme_ctx": None, "focus": cache.lists.get("focus", pd.DataFrame()),
            "targets": cache.lists.get("targets", pd.DataFrame()),
            "earnings": cache.lists.get("earnings", pd.DataFrame()),
            "ipo": cache.lists.get("ipo", pd.DataFrame()),
            "as_of": cache.as_of, "warnings": []}


CLOUD = c.cloud_mode()

if "scan" not in st.session_state:
    st.session_state["scan"] = c.load_published() if CLOUD else _load_from_cache()

scan = st.session_state["scan"]

# ---- header + refresh ----
col1, col2 = st.columns([6, 1])
with col1:
    c.header("Morning Dashboard", scan["as_of"] if scan else "—")
with col2:
    label = "Re-pull" if CLOUD else "Refresh"
    if st.button(label, use_container_width=True):
        st.session_state["scan"] = c.load_published() if CLOUD else c.full_scan()
        st.session_state.pop("watchlist_graded_key", None)
        st.rerun()

if scan:
    c.regime_line(scan["regime"].state, scan["regime"].rationale)
    c.warning_banner(scan["warnings"])
else:
    msg = ("No published scan found. The scheduled job writes "
           "<code>data/latest_scan.json</code>; check the data branch / Actions run."
           if CLOUD else
           "No scan yet today. Click <b>Refresh</b> to run a live scan (about a minute).")
    st.markdown(f"<div class='pp-empty'>{msg}</div>", unsafe_allow_html=True)
    c.disclaimer_footer()
    st.stop()

# ---- Focus ----
st.markdown("<div class='pp-section'>Today's Focus picks</div>", unsafe_allow_html=True)
focus = scan["focus"]
if focus is None or len(focus) == 0:
    st.markdown("<div class='pp-empty'>No names currently show a valid pattern with "
                "R:R ≥ 5:1. This is normal — most days nothing is at a clean entry.</div>",
                unsafe_allow_html=True)
else:
    for _, row in focus.iterrows():
        c.focus_card(row)

# ---- Earnings ----
st.markdown("<div class='pp-section'>Overnight earnings reactions</div>", unsafe_allow_html=True)
earn = scan["earnings"]
if earn is None or len(earn) == 0:
    st.markdown("<div class='pp-empty'>No gapped-up earnings reactions.</div>",
                unsafe_allow_html=True)
else:
    cols = [x for x in ["ticker", "sector", "price", "gap", "rel_volume", "rs"] if x in earn.columns]
    view = earn[cols]
    styled = view.style.background_gradient(subset=[x for x in ["rs"] if x in cols],
                                            cmap="Greens")
    st.dataframe(styled, use_container_width=True, hide_index=True)
    pick = st.selectbox("View chart for", ["—"] + view["ticker"].tolist(), key="earn_chart")
    if pick and pick != "—":
        c.focus_card(pd.Series({"ticker": pick, "sector": "", "pattern": "earnings reaction"}))

# ---- Watchlist ----
st.markdown("<div class='pp-section'>My watchlist</div>", unsafe_allow_html=True)
wl = store.load_watchlist()
if not wl:
    st.markdown("<div class='pp-empty'>Your watchlist is empty. Add tickers from the "
                "<b>My Picks</b> page and they'll be re-graded here each morning.</div>",
                unsafe_allow_html=True)
else:
    key = f"watchlist_graded::{','.join(wl)}::{'cloud' if CLOUD else 'live'}"
    if st.session_state.get("watchlist_graded_key") != key:
        ref_df, ref_date = c.reference_universe()
        with st.spinner("Re-grading your watchlist..."):
            graded = analyzer.analyze_picks(
                None if CLOUD else c.get_client(), wl, scan["regime"],
                reference_universe=ref_df, theme_ctx=scan.get("theme_ctx"),
                offline_universe=ref_df if CLOUD else None, cache_only=CLOUD)
        st.session_state["watchlist_graded"] = graded
        st.session_state["watchlist_graded_key"] = key
    for pr in st.session_state.get("watchlist_graded", []):
        c.pick_card(pr)
        if st.button(f"Remove {pr.ticker}", key=f"rm_{pr.ticker}"):
            store.remove_from_watchlist(pr.ticker)
            st.session_state.pop("watchlist_graded_key", None)
            st.rerun()

c.disclaimer_footer()
