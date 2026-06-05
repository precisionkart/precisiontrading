"""My Picks analyzer (spec Section 7B/11).

Paste/upload tickers; each is graded through the SAME engine. A+ setups get a
full Focus card, near-misses an amber pill, and failures a per-gate pass/fail
checklist with a plain-English verdict — never a rubber stamp. RS is ranked
against the last full-scan universe snapshot.
"""

import io
import sys, os

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as c
from pinpoint import store, analyzer
from pinpoint import regime as regime_mod


c.bootstrap("My Picks")

ref_df, ref_date = c.reference_universe()
ref_tag = f"grading against universe snapshot from {ref_date}" if ref_date else \
    "no snapshot yet — RS ranked against your picks only (run a Full Scan for a real reference)"

st.markdown(f"<div class='pp-h1'>My Picks <span class='dim'>Analyzer</span></div>"
            f"<div class='pp-asof'>{ref_tag}</div>", unsafe_allow_html=True)
st.write("")

# ---- input ----
text = st.text_area("Tickers (comma or newline separated)",
                    placeholder="AAPL, GOOGL, AGX, BAND, MEC, NVDA", height=90)
uploaded = st.file_uploader("…or upload a CSV (first column = tickers)", type=["csv"])
analyze = st.button("Analyze", type="primary")


def _parse_tickers() -> list[str]:
    raw = []
    if text:
        raw += [t for chunk in text.replace("\n", ",").split(",") for t in [chunk.strip()]]
    if uploaded is not None:
        try:
            df = pd.read_csv(uploaded)
            raw += [str(x) for x in df.iloc[:, 0].tolist()]
        except Exception:  # noqa: BLE001
            st.error("Could not read that CSV.")
    seen, out = set(), []
    for t in raw:
        t = t.strip().upper()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


CLOUD = c.cloud_mode()


def _run_analysis(tickers: list[str]) -> None:
    if CLOUD:
        # zero live calls: grade from the committed snapshot + cached OHLCV.
        pub = c.load_published()
        reg = pub["regime"] if pub else regime_mod.from_fundaments({})
        with st.spinner(f"Grading {len(tickers)} tickers from the latest snapshot..."):
            st.session_state["pick_results"] = analyzer.analyze_picks(
                None, tickers, reg, reference_universe=ref_df,
                offline_universe=ref_df, cache_only=True)
        return
    client = c.get_client()
    with st.spinner("Reading regime..."):
        reg = regime_mod.fetch_regime(client)
    theme_ctx = None
    with st.spinner("Ranking sector/theme ETFs (for the hot-theme layer)..."):
        try:
            from pinpoint import themes
            theme_ctx = themes.build_theme_context(client)
        except Exception:  # noqa: BLE001
            theme_ctx = None
    with st.spinner(f"Grading {len(tickers)} tickers through the Pinpoint engine..."):
        st.session_state["pick_results"] = analyzer.analyze_picks(
            client, tickers, reg, reference_universe=ref_df, theme_ctx=theme_ctx)


if analyze:
    tickers = _parse_tickers()
    if not tickers:
        st.warning("Paste at least one ticker.")
        st.stop()
    _run_analysis(tickers)

results = st.session_state.get("pick_results", [])
if results:
    n_ok = sum(1 for r in results if r.classification == "A+")
    n_near = sum(1 for r in results if r.classification == "near")
    n_fail = sum(1 for r in results if r.classification == "fail")
    st.markdown(f"<div class='pp-section'>{len(results)} graded — "
                f"{n_ok} A+, {n_near} near, {n_fail} not setups</div>",
                unsafe_allow_html=True)
    for pr in results:
        c.pick_card(pr)
        cols = st.columns([1, 5])
        with cols[0]:
            if st.button("Save", key=f"save_{pr.ticker}"):
                store.add_to_watchlist(pr.ticker)
                st.toast(f"{pr.ticker} added to watchlist")

c.disclaimer_footer()
