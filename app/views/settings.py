"""Settings view — placeholder for future config (Phase 7.6)."""

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as c
from pinpoint import __version__, store
from pinpoint.config import CONFIG

st.markdown("<div class='pp-h1'>Settings</div>", unsafe_allow_html=True)

# ---- Position sizing (Phase 11 — persists to data/user_settings.json) ----
st.markdown("<div class='pp-section'>Position sizing</div>", unsafe_allow_html=True)
st.markdown("<div class='pp-sub'>Risk-based sizing per the methodology — share count "
            "= account × risk% ÷ per-share risk. A research aid, not advice.</div>",
            unsafe_allow_html=True)
_us = store.load_user_settings()
sc1, sc2, sc3 = st.columns(3)
with sc1:
    acct = st.number_input("Account size ($)", min_value=0.0, value=float(_us["account"]),
                           step=1000.0, format="%.0f", key="set_account")
with sc2:
    risk = st.number_input("Risk per trade (%)", min_value=0.1, max_value=2.0,
                           value=float(_us["risk_pct"]), step=0.1, format="%.2f", key="set_risk")
with sc3:
    heat = st.number_input("Portfolio heat cap (%)", min_value=1.0, max_value=10.0,
                           value=float(_us["heat_cap_pct"]), step=0.5, format="%.1f", key="set_heat")
if (acct, risk, heat) != (float(_us["account"]), float(_us["risk_pct"]), float(_us["heat_cap_pct"])):
    store.save_user_settings({"account": acct, "risk_pct": risk, "heat_cap_pct": heat})
    st.session_state.pop("_sizing", None)            # invalidate cached sizing
    st.toast("Settings saved", icon="✅")
st.markdown(f"<div class='pp-sub'>At <b>{risk:.2f}%</b> risk on a "
            f"<b>${acct:,.0f}</b> account you risk <b>${acct*risk/100:,.0f}</b> per trade.</div>",
            unsafe_allow_html=True)

st.markdown("<div class='pp-section'>Scan thresholds</div>", unsafe_allow_html=True)
st.markdown("<div class='pp-sub'>Read-only for now — thresholds live in "
            "<code>pinpoint/config.py</code> (the single source of truth).</div>",
            unsafe_allow_html=True)

mode = "Read-only cloud (latest_scan.json)" if c.cloud_mode() else "Local (live Finviz)"
g = CONFIG.gates
rows = [
    ("Mode", mode),
    ("Version", f"v{__version__}"),
    ("Price gate", f"> ${g.min_price:.0f}"),
    ("Avg volume gate", f">= {g.min_avg_volume:,}"),
    ("RVOL gate", f"> {g.min_rel_volume}"),
    ("RS proxy gate", f"> {g.min_rs_rating}"),
    ("Near-high gate", f"<= {g.max_pct_below_high:.0f}% below 52w high"),
    ("Min R:R for Focus", f"{CONFIG.entry.min_reward_risk:.0f}:1"),
    ("Finviz request delay", f"{CONFIG.network.request_delay_s:.0f}s "
     f"(env PINPOINT_REQUEST_DELAY)"),
]
html = "".join(
    f"<div class='pp-check'><span>{k}</span><span class='detail'>{v}</span></div>"
    for k, v in rows)
st.markdown(f"<div class='pp-checks'>{html}</div>", unsafe_allow_html=True)

st.markdown("<div class='pp-section'>Coming later</div>", unsafe_allow_html=True)
st.markdown("<div class='pp-sub'>Editable thresholds, theme-basket tuning, Discord "
            "alerts (Phase 9), and the Option-B live-cloud toggle.</div>",
            unsafe_allow_html=True)
