"""Full Scan — run Targets/Focus/Earnings/IPO on demand and download outputs.

No auto-run on page load (Finviz rate limits). Results cache in session_state.
"""

import io
import os
import sys
from datetime import datetime

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as c
from pinpoint import output, report
from pinpoint.config import CONFIG


c.bootstrap("Full Scan")

st.markdown("<div class='pp-h1'>Full Scan</div>", unsafe_allow_html=True)
st.write("")

CLOUD = c.cloud_mode()
cols = st.columns([1, 1, 4])
with cols[0]:
    run = st.button("Run Scan", type="primary", use_container_width=True, disabled=CLOUD,
                    help="Disabled on the read-only cloud app — scans run on a schedule."
                    if CLOUD else None)
with cols[1]:
    if CLOUD:
        if st.button("Re-pull", use_container_width=True):
            st.session_state.pop("full_scan", None)
            st.rerun()
        ignore_rvol = False
    else:
        ignore_rvol = st.toggle("Ignore RVOL", help="Drop the relative-volume gate "
                                "(weekend / evening prep)")

if run and not CLOUD:
    st.session_state["full_scan"] = c.full_scan(ignore_rvol=ignore_rvol)

# In read-only cloud mode, render from the latest published snapshot.
if CLOUD and "full_scan" not in st.session_state:
    pub = c.load_published()
    if pub is not None:
        st.session_state["full_scan"] = pub

scan = st.session_state.get("full_scan")
if not scan:
    msg = ("No published scan found (data/latest_scan.json). Check the scheduled job."
           if CLOUD else
           "Click <b>Run Scan</b> to fetch live Finviz screens and build all four "
           "lists. (Not auto-run — Finviz rate-limits.)")
    st.markdown(f"<div class='pp-empty'>{msg}</div>", unsafe_allow_html=True)
    c.disclaimer_footer()
    st.stop()

c.header("Full Scan", scan["as_of"])
c.regime_line(scan["regime"].state, scan["regime"].rationale)
c.warning_banner(scan["warnings"])


def _section(title: str, df: pd.DataFrame, gradient_col: str | None, expanded: bool):
    n = 0 if df is None else len(df)
    with st.expander(f"{title} ({n})", expanded=expanded):
        if n == 0:
            st.markdown("<div class='pp-empty'>(empty)</div>", unsafe_allow_html=True)
            return
        if gradient_col and gradient_col in df.columns:
            st.dataframe(df.style.background_gradient(subset=[gradient_col], cmap="Greens"),
                         use_container_width=True, hide_index=True)
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)


_section("Focus", scan["focus"], "pinpoint_score", expanded=True)
_section("Targets", scan["targets"], "pinpoint_score", expanded=True)
_section("Earnings", scan["earnings"], "rs", expanded=True)
_section("IPO Watchlist", scan["ipo"], None, expanded=True)

# ---- downloads ----
import scan as scan_cli   # reuse the CLI's chart+report+xlsx writer  # noqa: E402

st.markdown("<div class='pp-section'>Download</div>", unsafe_allow_html=True)
date = datetime.now().strftime("%Y-%m-%d")


class _RegimeShim:
    def __init__(self, rv):
        self.state = rv.state
        self.rationale = rv.rationale


def _csv_bytes(df):
    return df.to_csv(index=False).encode("utf-8") if df is not None and len(df) else b""


dcols = st.columns(3)
with dcols[0]:
    st.download_button("Focus CSV", _csv_bytes(scan["focus"]), f"focus_{date}.csv",
                       "text/csv", use_container_width=True)
with dcols[1]:
    st.download_button("Targets CSV", _csv_bytes(scan["targets"]), f"targets_{date}.csv",
                       "text/csv", use_container_width=True)
with dcols[2]:
    xlsx_path = os.path.join(CONFIG.paths.output_dir, f"pinpoint_{date}.xlsx")
    output.write_xlsx(scan["focus"], scan["targets"], scan["earnings"], xlsx_path)
    xbytes = open(xlsx_path, "rb").read() if os.path.exists(xlsx_path) else b""
    st.download_button("Workbook XLSX", xbytes, f"pinpoint_{date}.xlsx",
                       use_container_width=True, disabled=not xbytes)

if st.button("Build full HTML report (charts + cards)"):
    with st.spinner("Rendering charts + HTML report..."):
        path = scan_cli.render_outputs(scan["focus"], scan["targets"], scan["earnings"],
                                       _RegimeShim(scan["regime"]), date, scan["as_of"],
                                       ipo=scan["ipo"])
    if path and os.path.exists(path):
        st.download_button("Download report.html", open(path, "rb").read(),
                           f"report_{date}.html", "text/html")
        st.success(f"Report written to {path}")

c.disclaimer_footer()
