"""Watchlist view — full management of saved names (Phase 7.6, promoted to its
own page). Each saved ticker is graded fresh and shown as a compact card."""

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as c
import dashboard_logic as dl
from pinpoint import store, analyzer
from pinpoint import themes as themes_mod
from pinpoint import regime as regime_mod

CLOUD = c.cloud_mode()
scan = st.session_state.get("scan")

st.markdown("<div class='pp-h1'>Watchlist</div>", unsafe_allow_html=True)
wl = store.load_watchlist()
st.markdown(f"<div class='pp-sub'>{len(wl)} saved · re-graded fresh</div>", unsafe_allow_html=True)

# add box
add = st.text_input("Add ticker(s)", placeholder="NVDA, CRWD", label_visibility="collapsed")
if st.button("Add", key="wl_add") and add:
    for t in add.replace("\n", ",").split(","):
        if t.strip():
            store.add_to_watchlist(t.strip())
    st.session_state.pop("wlpage_key", None)
    st.rerun()

if not wl:
    st.markdown("<div class='pp-empty'>Your watchlist is empty. ★ Save names from the "
                "Dashboard cards or My Picks.</div>", unsafe_allow_html=True)
    c.disclaimer_footer()
    st.stop()

reg = scan["regime"] if scan else regime_mod.from_fundaments({})
theme_ctx = themes_mod.context_from_list(scan.get("themes", [])) if scan else None
ref_df, _ = c.reference_universe()
key = f"wlpage::{','.join(wl)}::{'c' if CLOUD else 'l'}"
if st.session_state.get("wlpage_key") != key:
    with st.spinner("Grading watchlist…"):
        st.session_state["wlpage"] = analyzer.analyze_picks(
            None if CLOUD else c.get_client(), wl, reg,
            reference_universe=ref_df, theme_ctx=theme_ctx,
            offline_universe=ref_df if CLOUD else None, cache_only=CLOUD)
    st.session_state["wlpage_key"] = key

for pr in st.session_state.get("wlpage", []):
    c.pick_card(pr)
    if st.button(f"Remove {pr.ticker}", key=f"rm_{pr.ticker}"):
        store.remove_from_watchlist(pr.ticker)
        st.session_state.pop("wlpage_key", None)
        st.session_state.pop("wlstrip_key", None)
        st.rerun()

c.disclaimer_footer()
