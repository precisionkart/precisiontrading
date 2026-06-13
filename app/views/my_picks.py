"""My Picks view — grade pasted tickers through the same engine."""

import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as c
from pinpoint import store, analyzer
from pinpoint import regime as regime_mod

CLOUD = c.cloud_mode()
ref_df, ref_date = c.reference_universe()
ref_tag = (f"grading against universe snapshot from {ref_date}" if ref_date else
           "no snapshot yet — RS ranked against your picks only")

c.page_header("My Picks <span class='dim'>Analyzer</span>", subtitle=ref_tag)
st.write("")

text = st.text_area("Tickers (comma or newline separated)",
                    placeholder="AAPL, GOOGL, AGX, BAND, MEC, NVDA", height=88)
uploaded = st.file_uploader("…or upload a CSV (first column = tickers)", type=["csv"])
analyze = st.button("Analyze", type="primary")


def _parse():
    raw = []
    if text:
        raw += [t.strip() for chunk in text.replace("\n", ",").split(",") for t in [chunk]]
    if uploaded is not None:
        try:
            raw += [str(x) for x in pd.read_csv(uploaded).iloc[:, 0].tolist()]
        except Exception:  # noqa: BLE001
            st.error("Could not read that CSV.")
    seen, out = set(), []
    for t in raw:
        t = t.strip().upper()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _run(tickers):
    if CLOUD:
        pub = c.load_published()
        reg = pub["regime"] if pub else regime_mod.from_fundaments({})
        with st.spinner(f"Grading {len(tickers)} tickers from the latest snapshot…"):
            st.session_state["pick_results"] = analyzer.analyze_picks(
                None, tickers, reg, reference_universe=ref_df,
                offline_universe=ref_df, cache_only=True)
        return
    client = c.get_client()
    with st.spinner("Reading regime…"):
        reg = regime_mod.fetch_regime(client)
    theme_ctx = None
    try:
        from pinpoint import themes
        with st.spinner("Ranking themes…"):
            theme_ctx = themes.build_theme_context(client)
    except Exception:  # noqa: BLE001
        theme_ctx = None
    with st.spinner(f"Grading {len(tickers)} tickers…"):
        st.session_state["pick_results"] = analyzer.analyze_picks(
            client, tickers, reg, reference_universe=ref_df, theme_ctx=theme_ctx)


if analyze:
    tk = _parse()
    if not tk:
        st.warning("Paste at least one ticker.")
        st.stop()
    _run(tk)

results = st.session_state.get("pick_results", [])
if results:
    n_ok = sum(1 for r in results if r.classification == "A+")
    n_near = sum(1 for r in results if r.classification == "near")
    n_fail = sum(1 for r in results if r.classification == "fail")
    st.markdown(f"<div class='pp-section'>{len(results)} graded — {n_ok} A+, "
                f"{n_near} near, {n_fail} not setups</div>", unsafe_allow_html=True)
    for pr in results:
        c.pick_card(pr)
        if st.button("★ Save", key=f"save_{pr.ticker}"):
            store.add_to_watchlist(pr.ticker)
            st.toast(f"{pr.ticker} added to watchlist")

    st.markdown("<div class='pp-section'>Copy to TradingView</div>", unsafe_allow_html=True)
    actionable = [r.ticker for r in results if r.classification in ("A+", "near")]
    c.tv_block("Actionable (A+ / near)", actionable, key="tv_mp_act")
    c.tv_block("All graded", [r.ticker for r in results], key="tv_mp_all")

c.disclaimer_footer()
