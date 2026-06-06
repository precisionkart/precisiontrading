"""Settings view — placeholder for future config (Phase 7.6)."""

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as c
from pinpoint import __version__
from pinpoint.config import CONFIG

st.markdown("<div class='pp-h1'>Settings</div>", unsafe_allow_html=True)
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
