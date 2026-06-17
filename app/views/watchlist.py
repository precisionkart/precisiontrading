"""Watchlist view — the trading board (Phase 11 Watchlist v2).

Each saved name is graded fresh and shown as a status card: status pill
(ACTIVE/WATCH/EARNINGS/DORMANT), live price + today's change, the trade plan when
active, an RS-history sparkline, "since added" return, days-on-list, an
inline-editable notes field, and a ★ remove toggle. Sortable + filterable.
"""

import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as c
import dashboard_logic as dl
from pinpoint import store, analyzer, watchlist as wl
from pinpoint import earnings_watch as ew
from pinpoint import themes as themes_mod
from pinpoint import regime as regime_mod

CLOUD = c.cloud_mode()
scan = st.session_state.get("scan")

c.page_header("Watchlist")
entries = {e["ticker"]: e for e in wl.load_entries()}
names = list(entries)
st.markdown(f"<div class='pp-sub'>{len(names)} on your board · re-graded fresh</div>",
            unsafe_allow_html=True)

add = st.text_input("Add ticker(s)", placeholder="NVDA, CRWD", label_visibility="collapsed")
if st.button("Add", key="wl_add") and add:
    for t in add.replace("\n", ",").split(","):
        if t.strip():
            wl.add(t.strip())
    for k in ("wlpage_key", "_wl_members"):
        st.session_state.pop(k, None)
    st.rerun()

if not names:
    st.markdown("<div class='pp-empty'>Your board is empty. ★ Save names from the "
                "Dashboard cards, earnings panels, or My Picks.</div>", unsafe_allow_html=True)
    c.disclaimer_footer()
    st.stop()

# ---- grade all names fresh ----
reg = scan["regime"] if scan else regime_mod.from_fundaments({})
theme_ctx = themes_mod.context_from_list(scan.get("themes", [])) if scan else None
ref_df, _ = c.reference_universe()
# Re-grade saved names from the cached snapshot when we have one (fast, no live
# Finviz); fall back to a live grade only if there's no snapshot and we're local.
use_offline = CLOUD or (ref_df is not None and len(ref_df) > 0)
key = f"wlpage::{','.join(names)}::{'off' if use_offline else 'live'}"
if st.session_state.get("wlpage_key") != key:
    with st.spinner("Grading watchlist…"):
        st.session_state["wlpage"] = analyzer.analyze_picks(
            None if use_offline else c.get_client(), names, reg,
            reference_universe=ref_df, theme_ctx=theme_ctx,
            offline_universe=ref_df if use_offline else None, cache_only=use_offline)
    st.session_state["wlpage_key"] = key
graded = {pr.ticker: pr for pr in st.session_state.get("wlpage", [])}

focus_df = scan.get("focus") if scan else None
targets_df = scan.get("targets") if scan else None
earnings_ctx = ew.active_ctx()

_PILL = {"ACTIVE": ("🔥 ACTIVE SETUP", "active"), "EARNINGS": ("📈 EARNINGS FLAG", "earn"),
         "WATCH": ("⚡ WATCH", "watch"), "DORMANT": ("💤 DORMANT", "dormant")}
_ORDER = {"ACTIVE": 0, "EARNINGS": 1, "WATCH": 2, "DORMANT": 3}


def _row(tk):
    pr = graded.get(tk)
    status = wl.compute_status(tk, focus_df, targets_df, earnings_ctx)
    price = chg = None
    if pr is not None and pr.daily is not None and len(pr.daily):
        closes = list(pr.daily["Close"])
        price = float(closes[-1])
        if len(closes) >= 2:
            chg = (closes[-1] / closes[-2] - 1.0) * 100.0
    return {"tk": tk, "pr": pr, "status": status, "price": price, "chg": chg,
            "rs": getattr(pr, "rs", None), "days": wl.days_on(tk),
            "since": wl.since_added_pct(tk), "notes": entries[tk].get("notes", ""),
            "dormant_streak": wl.dormant_streak(tk)}


rows = [_row(tk) for tk in names]

# ---- sort + filter controls ----
fc, sc = st.columns([3, 1.4], vertical_alignment="center")
with fc:
    filt = st.radio("Filter", ["All", "🔥 Active", "⚡ Watch", "📈 Earnings", "💤 Dormant"],
                    horizontal=True, label_visibility="collapsed", key="wl_filter")
with sc:
    sort = st.selectbox("Sort", ["Status priority", "RS ↓", "Days on list ↓", "A→Z"],
                        label_visibility="collapsed", key="wl_sort")

_fmap = {"🔥 Active": "ACTIVE", "⚡ Watch": "WATCH", "📈 Earnings": "EARNINGS", "💤 Dormant": "DORMANT"}
if filt in _fmap:
    rows = [r for r in rows if r["status"] == _fmap[filt]]
if sort == "Status priority":
    rows.sort(key=lambda r: (_ORDER.get(r["status"], 9), -(r["rs"] or 0)))
elif sort == "RS ↓":
    rows.sort(key=lambda r: -(r["rs"] or 0))
elif sort == "Days on list ↓":
    rows.sort(key=lambda r: -(r["days"] or 0))
else:
    rows.sort(key=lambda r: r["tk"])

st.markdown(f"<div class='pp-section'>{len(rows)} shown</div>", unsafe_allow_html=True)

# ---- status grid ----
for i, r in enumerate(rows):
    tk, pr, status = r["tk"], r["pr"], r["status"]
    pill_txt, pill_cls = _PILL[status]
    chg_html = ""
    if r["chg"] is not None:
        cc = "up" if r["chg"] >= 0 else "down"
        chg_html = f"<span class='chg {cc}'>{'▲' if r['chg']>=0 else '▼'} {abs(r['chg']):.1f}%</span>"
    px = f"${r['price']:,.2f}" if r["price"] is not None else "—"
    pat = (pr.pattern.split(' /')[0] if pr and pr.pattern else "—")
    # trade plan (active only)
    plan = ""
    if status == "ACTIVE" and pr is not None:
        sh, dr = c.shares_for(pr.entry, pr.stop)
        plan = (f"<div class='pp-wl-plan'>E ${pr.entry:,.2f} · X ${pr.stop:,.2f} · "
                f"T ${pr.target:,.2f} · {pr.reward_risk:.1f}:1"
                f"{f' · {sh} sh' if sh else ''}</div>") if pr.entry and pr.stop else ""
    spark = dl.sparkline_svg(wl.rs_series(tk, 30)) if len(wl.rs_series(tk, 30)) >= 2 else ""
    since = (f"<span class='pp-wl-since {'up' if r['since']>=0 else 'down'}'>"
             f"Since added: {'+' if r['since']>=0 else ''}{r['since']:.1f}%</span>"
             if r["since"] is not None else "")
    days = f"{r['days']}d on list" if r["days"] is not None else ""
    dormant_hint = (" · <span class='pp-wl-hint'>consider removing</span>"
                    if r["dormant_streak"] >= wl.DORMANT_CONSECUTIVE_DAYS else "")

    head, starc = st.columns([14, 1], vertical_alignment="center")
    head.markdown(
        f"<div class='pp-wl-card'>"
        f"<div class='pp-wl-top'><span class='pp-wl-tk'>{c._html.escape(tk)}</span>"
        f"{c.rs_chip_html(r['rs'])}<span class='pp-wl-px'>{px} {chg_html}</span>"
        f"<span class='pp-pill {pill_cls}'>{pill_txt}</span>"
        f"<span class='pp-wl-pat'>{c._html.escape(pat)}</span>"
        f"<span class='pp-wl-spark'>{spark}</span></div>"
        f"{plan}"
        f"<div class='pp-wl-meta'>{since}{(' · ' if since and days else '')}{days}{dormant_hint}</div>"
        f"</div>", unsafe_allow_html=True)
    with starc:
        c.star_button(tk, key=f"wlb_{i}_{tk}")
    # inline notes (max 280; saves on change)
    note = st.text_input(f"notes_{tk}", value=r["notes"], max_chars=wl.NOTES_MAX,
                         placeholder="notes…", label_visibility="collapsed", key=f"note_{tk}")
    if note != r["notes"]:
        wl.set_notes(tk, note)
    # book exit signals if firing
    if pr is not None and getattr(pr, "exit_signals", None):
        st.markdown(c.exit_signal_pills_html(pr.exit_signals), unsafe_allow_html=True)

c.disclaimer_footer()
