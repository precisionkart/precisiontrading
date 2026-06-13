"""Full Scan view — run Targets/Focus/Earnings/IPO on demand + downloads."""

import os
import sys
from datetime import datetime

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as c
from pinpoint import output, store
from pinpoint.config import CONFIG

CLOUD = c.cloud_mode()
c.page_header("Full Scan")
st.write("")

cols = st.columns([1, 1, 4])
with cols[0]:
    run = st.button("Run Scan", type="primary", use_container_width=True, disabled=CLOUD)
with cols[1]:
    if CLOUD:
        if st.button("Re-pull", use_container_width=True):
            st.session_state.pop("full_scan", None)
            st.rerun()
        ignore_rvol = False
    else:
        ignore_rvol = st.toggle("Ignore RVOL")

if run and not CLOUD:
    st.session_state["full_scan"] = c.full_scan(ignore_rvol=ignore_rvol)
if CLOUD and "full_scan" not in st.session_state:
    pub = c.load_published()
    if pub is not None:
        st.session_state["full_scan"] = pub

fs = st.session_state.get("full_scan")
if not fs:
    msg = ("No published scan found." if CLOUD else
           "Click <b>Run Scan</b> to fetch live Finviz screens (not auto-run — rate limits).")
    st.markdown(f"<div class='pp-empty'>{msg}</div>", unsafe_allow_html=True)
    c.disclaimer_footer()
    st.stop()

c.regime_line(fs["regime"].state, fs["regime"].rationale)
c.warning_banner(fs.get("warnings"))


def _section(title, df, grad):
    n = 0 if df is None else len(df)
    with st.expander(f"{title} ({n})", expanded=True):
        if n == 0:
            st.markdown("<div class='pp-empty'>(empty)</div>", unsafe_allow_html=True)
            return
        if grad and grad in df.columns:
            st.dataframe(df.style.background_gradient(subset=[grad], cmap="Greens"),
                         use_container_width=True, hide_index=True)
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)


_section("Focus", fs.get("focus"), "pinpoint_score")
_section("Targets", fs.get("targets"), "pinpoint_score")
_section("Earnings", fs.get("earnings"), "rs")
_section("IPO Watchlist", fs.get("ipo"), None)

st.markdown("<div class='pp-section'>Download</div>", unsafe_allow_html=True)
date = datetime.now().strftime("%Y-%m-%d")


def _csv(df):
    return df.to_csv(index=False).encode("utf-8") if df is not None and len(df) else b""


d = st.columns(3)
d[0].download_button("Focus CSV", _csv(fs.get("focus")), f"focus_{date}.csv", "text/csv",
                     use_container_width=True)
d[1].download_button("Targets CSV", _csv(fs.get("targets")), f"targets_{date}.csv", "text/csv",
                     use_container_width=True)
with d[2]:
    xp = os.path.join(CONFIG.paths.output_dir, f"pinpoint_{date}.xlsx")
    output.write_xlsx(fs.get("focus"), fs.get("targets"), fs.get("earnings"), xp)
    xb = open(xp, "rb").read() if os.path.exists(xp) else b""
    st.download_button("Workbook XLSX", xb, f"pinpoint_{date}.xlsx",
                       use_container_width=True, disabled=not xb)

st.markdown("<div class='pp-section'>Copy to TradingView</div>", unsafe_allow_html=True)


def _tickers(df):
    return df["ticker"].dropna().tolist() if df is not None and "ticker" in getattr(df, "columns", []) else []


c.tv_block("Focus", _tickers(fs.get("focus")), key="tv_fs_focus")
c.tv_block("Targets", _tickers(fs.get("targets")), key="tv_fs_targets")
c.tv_block("Earnings", _tickers(fs.get("earnings")), key="tv_fs_earn")
c.tv_block("IPO watchlist", _tickers(fs.get("ipo")), key="tv_fs_ipo")

c.disclaimer_footer()
