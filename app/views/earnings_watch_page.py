"""Earnings Watch view — the active earnings_watch.json (Phase 9).

Every gap-up in the 1-4 week window with its current status: forming a flag,
breaking out (triggered), or expiring soon. This is the forward-track that
connects the Earnings list to Focus.
"""

import os
import sys
from datetime import date

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as c
from pinpoint import earnings_watch as ew

c.page_header("Earnings Watch")
st.markdown("<div class='pp-sub'>Gap-ups tracked through the 1-4 week flag window. "
            "A breakout here is the strategy's highest-edge setup.</div>",
            unsafe_allow_html=True)

store = ew.load_store()
if not store:
    st.markdown("<div class='pp-empty'>No earnings gap-ups tracked yet. Run a live "
                "scan (or the scheduled job) — strong gap-ups get persisted here.</div>",
                unsafe_allow_html=True)
    c.disclaimer_footer()
    st.stop()

today = date.today()
active = {e["ticker"] for e in ew.load_active(today)}


def _days(gd):
    d = ew._parse(gd)
    return (today - d).days if d else None


def _window(gd):
    n = _days(gd)
    if n is None:
        return "—"
    if n < ew.WINDOW_START_DAYS:
        return f"forming ({n}d)"
    if n <= ew.WINDOW_END_DAYS:
        return f"active ({n}d)"
    return f"expired ({n}d)"


rows = []
for e in sorted(store, key=lambda x: x.get("gap_date", ""), reverse=True):
    status = e.get("status", "active")
    label = ("breaking out" if status == "triggered" else
             "expired" if status == "expired" else _window(e.get("gap_date")))
    rows.append({
        "Ticker": e["ticker"], "Gap %": e.get("gap_pct"),
        "Gap date": str(e.get("gap_date", ""))[:10],
        "Days": _days(e.get("gap_date")), "Status": label,
        "EMA zone": e.get("ema_zone") or "—", "Sector": e.get("sector") or "—",
        "In window": "★" if e["ticker"] in active else "",
    })

df = pd.DataFrame(rows)
n_active = len(active)
n_trig = sum(1 for e in store if e.get("status") == "triggered")
st.markdown(f"<div class='pp-section'>{len(store)} tracked · {n_active} in window · "
            f"{n_trig} breaking out</div>", unsafe_allow_html=True)
st.dataframe(df, use_container_width=True, hide_index=True)

# ★ quick-add for tracked names (dataframes can't host per-row buttons)
tracked = [e["ticker"] for e in sorted(store, key=lambda x: x.get("gap_date", ""), reverse=True)]
if tracked:
    st.markdown("<div class='pp-section' style='font-size:12px'>★ Add to watchlist</div>",
                unsafe_allow_html=True)
    cols = st.columns(min(6, len(tracked)))
    for j, tk in enumerate(tracked):
        with cols[j % len(cols)]:
            nc, sc = st.columns([2, 1], vertical_alignment="center")
            nc.markdown(f"<span class='pp-tierpill'>{c._html.escape(str(tk))}</span>",
                        unsafe_allow_html=True)
            with sc:
                c.star_button(tk, key=f"ew_{j}_{tk}")

c.disclaimer_footer()
