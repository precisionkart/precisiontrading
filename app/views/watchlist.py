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
from pinpoint import ohlcv as ohlcv_mod
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
    # paper-trade: mark an ACTIVE setup as "would have traded"
    if status == "ACTIVE" and pr is not None and pr.entry and pr.stop and pr.target:
        open_tks = {t["ticker"] for t in wl.load_trades()
                    if str(t.get("status")) in ("open", "open_aged")}
        if tk in open_tks:
            st.caption(f"📝 open paper-trade already logged for {tk}")
        elif st.button(f"📝 Mark {tk} as would-have-traded", key=f"mark_{tk}"):
            sh, _dr = c.shares_for(pr.entry, pr.stop)
            wl.mark_paper_trade(tk, pr.entry, pr.stop, pr.target, pr.reward_risk, sh)
            st.toast(f"Marked {tk} as would-have-traded")
            st.rerun()

# ---- PAPER TRADES ----
st.markdown("<div class='pp-section'>📝 Paper trades</div>", unsafe_allow_html=True)
# resolve open trades against cached OHLCV (idempotent), then show
wl.resolve_trades(lambda t: ohlcv_mod.fetch_daily(t, cache_only=True).df)
trades = sorted(wl.load_trades(), key=lambda t: t.get("marked_date", ""), reverse=True)
if not trades:
    st.markdown("<div class='pp-empty'>No paper trades yet. Mark an ACTIVE setup above "
                "as 'would have traded' to start your track record.</div>", unsafe_allow_html=True)
else:
    s = wl.paper_stats(trades)
    wr = f"{s['win_rate']:.1f}%" if s["win_rate"] is not None else "—"
    ar = f"{'+' if (s['avg_r'] or 0) >= 0 else ''}{s['avg_r']:.1f}" if s["avg_r"] is not None else "—"
    st.markdown(
        f"<div class='pp-tiercount'>Total: {s['total']} · Open: {s['open']} · "
        f"Won: {s['won']} · Lost: {s['lost']} · Win rate: {wr} · Avg R: {ar}</div>",
        unsafe_allow_html=True)
    _sc = {"won": "won", "lost": "lost", "open": "openp", "open_aged": "openp"}
    _sl = {"won": "WON", "lost": "LOST", "open": "OPEN", "open_aged": "OPEN (aged)"}
    rows_html = []
    for t in trades:
        stt = str(t.get("status", "open"))
        ra = t.get("r_achieved")
        ra_txt = (f"{'+' if ra >= 0 else ''}{ra:.1f}R" if isinstance(ra, (int, float)) else "—")
        rows_html.append(
            f"<div class='pp-row pt {_sc.get(stt,'openp')}'>"
            f"<span class='tk'>{c._html.escape(str(t.get('ticker')))}</span>"
            f"<span class='px'>{str(t.get('marked_date',''))[:10]}</span>"
            f"<span class='pt-c'>E ${t.get('entry'):,.2f}</span>"
            f"<span class='pt-c'>X ${t.get('stop'):,.2f}</span>"
            f"<span class='pt-c'>T ${t.get('target'):,.2f}</span>"
            f"<span class='pt-status {_sc.get(stt,'openp')}'>{_sl.get(stt,'OPEN')}</span>"
            f"<span class='pt-r'>{ra_txt}</span></div>")
    st.markdown("<div style='display:flex;flex-direction:column;gap:5px'>"
                + "".join(rows_html) + "</div>", unsafe_allow_html=True)

c.disclaimer_footer()
