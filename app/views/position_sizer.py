"""Position Sizer view — fixed-risk share sizing + live-setup lookup.

Mode A (manual): type account / risk% / entry / stop and the shares, position
value, R-targets and the .89 liquidity stop update instantly — fully offline.
Mode B (ticker): pull fresh OHLCV, detect the best current pattern, and
auto-populate the calculator from the entry/stop the engine derives.

Reuses the production engine end-to-end: entries.liquidity_stop / compute_setup,
patterns.best_pattern, ohlcv.fetch_daily + add_moving_averages, and
store.load/save_user_settings (the same store the Settings page uses, so account
size persists across pages and restarts).
"""

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as c
from pinpoint import store
from pinpoint import entries as entries_mod
from pinpoint import patterns as patterns_mod
from pinpoint import ohlcv as ohlcv_mod
from pinpoint import position_sizing as sizing_mod
from pinpoint.config import CONFIG

c.page_header("Position Sizer")
st.markdown("<div class='pp-sub'>Risk-based share sizing — shares = account × risk% ÷ "
            "per-share risk. A research aid, not advice.</div>", unsafe_allow_html=True)

# ---- persisted account / risk (synced with Settings via user_settings.json) ----
_us = store.load_user_settings()
if "ps_account" not in st.session_state:
    st.session_state["ps_account"] = float(_us.get("account", 100_000) or 100_000)
if "ps_risk" not in st.session_state:
    st.session_state["ps_risk"] = float(_us.get("risk_pct", 0.5) or 0.5)


def _fmt(x, money=True):
    if x is None or x != x:
        return "—"
    return f"${x:,.2f}" if money else f"{x:,.2f}"


# =====================================================================
# TRADE TICKET — shown when a setup was sent from the Dashboard ("Trade This")
# =====================================================================
_active = st.session_state.get("active_trade")
if _active:
    acct = float(st.session_state["ps_account"]); rp = float(st.session_state["ps_risk"])
    entry = _active.get("entry"); stop = _active.get("stop")
    rr = _active.get("reward_risk")
    tk = _active.get("ticker", "")
    risk_ps = (entry - stop) if (entry and stop) else float("nan")
    cur = _active.get("current_price")
    rec = c.trade_recommendation(_active, current_price=cur)
    shares = sizing_mod.compute_shares(acct, rp, entry, stop) if (entry and stop) else 0
    cost = shares * entry if entry else 0.0
    buy_stop = entry
    limit = round(entry * 1.01, 2) if entry else None
    t3 = entry + 3 * risk_ps if risk_ps == risk_ps else None
    t5 = entry + 5 * risk_ps if risk_ps == risk_ps else None
    full_t = entry + rr * risk_ps if (rr and risk_ps == risk_ps) else None
    triggered = bool(cur and entry and cur >= entry)

    st.markdown(f"<div class='pp-ticket'><div class='pp-ticket-h'>📋 TRADE TICKET</div>"
                f"<div class='pp-ps-h'>{c._html.escape(tk)} — {c._html.escape(_active.get('pattern',''))}</div>"
                f"<div class='pp-sub'>RS {(_active.get('rs') or 0):.0f} · Score "
                f"{(_active.get('score') or 0):.1f} · {c._html.escape(_active.get('sector') or '—')} · "
                f"{c._html.escape(str(_active.get('regime','')).upper())} regime</div></div>",
                unsafe_allow_html=True)

    # recommendation
    checks_html = "".join(
        f"<div class='chk'>{'✓' if ok else '✗'} {c._html.escape(txt)}</div>"
        for ok, txt in rec["checks"])
    note = f"<div class='pp-sub'>{c._html.escape(rec.get('note',''))}</div>" if rec.get("note") else ""
    st.markdown(f"<div class='pp-rec {rec['css']}'><div class='pp-rec-h'>{rec['title']}</div>"
                f"{checks_html}{note}</div>", unsafe_allow_html=True)

    # order instructions
    trig_html = (f"<div class='pp-ok'>✅ Triggered — price ${cur:,.2f} ≥ entry ${entry:,.2f}</div>"
                 if triggered else
                 f"<div class='pp-warn'>⚠️ Not triggered yet — current "
                 f"{_fmt(cur)} &lt; entry {_fmt(entry)}. Set the buy stop and WAIT.</div>")
    st.markdown(
        f"<div class='pp-ps-card'><div class='pp-ps-sub'>── Order instructions ──</div>"
        f"<div class='pp-ps-grid'>"
        f"<span>Order type</span><b>BUY STOP LIMIT</b>"
        f"<span>Buy stop at</span><b>{_fmt(buy_stop)}</b>"
        f"<span>Limit price (1% above)</span><b>{_fmt(limit)}</b>"
        f"<span>Stop loss</span><b class='red'>{_fmt(stop)}</b>"
        f"<span>Shares</span><b class='big'>{shares:,}</b>"
        f"<span>Est. cost</span><b>{_fmt(cost)}</b>"
        f"</div>{trig_html}</div>", unsafe_allow_html=True)

    # targets
    st.markdown(
        f"<div class='pp-ps-card'><div class='pp-ps-sub'>── Profit targets ──</div>"
        f"<div class='pp-ps-grid'>"
        f"<span>3R → trim 20%</span><b class='green'>{_fmt(t3)}</b>"
        f"<span>5R → trim 30%</span><b class='green'>{_fmt(t5)}</b>"
        f"<span>Full target → exit rest</span><b class='green'>{_fmt(full_t)}</b>"
        f"<span>Trail</span><b>close below 10 EMA = EXIT ALL</b>"
        f"</div></div>", unsafe_allow_html=True)

    # actions
    a1, a2, a3, a4 = st.columns(4)
    with a1:
        if st.button("📋 Copy details", key="tk_copy", use_container_width=True):
            st.session_state["tk_show_copy"] = True
    with a2:
        if st.button("⭐ Watchlist", key="tk_wl", use_container_width=True):
            store.add_to_watchlist(tk)
            for k in ("_wl_members", "wlpage_key"):
                st.session_state.pop(k, None)
            st.toast(f"Added {tk} to watchlist")
    with a3:
        if st.button("✅ Mark as placed", key="tk_place", use_container_width=True, type="primary"):
            st.session_state["tk_placing"] = True
    with a4:
        if st.button("✗ Skip trade", key="tk_skip", use_container_width=True):
            for k in ("active_trade", "tk_placing", "tk_show_copy"):
                st.session_state.pop(k, None)
            st.rerun()

    if st.session_state.get("tk_show_copy"):
        st.code(
            f"{tk} — Pinpoint Setup\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Pattern:   {_active.get('pattern','')}\n"
            f"RS Rating: {(_active.get('rs') or 0):.0f}\n"
            f"Score:     {(_active.get('score') or 0):.1f}\n"
            f"Regime:    {str(_active.get('regime','')).upper()}\n\n"
            f"ORDER:\n"
            f"Buy stop:  {_fmt(buy_stop)}\nLimit:     {_fmt(limit)}\n"
            f"Stop loss: {_fmt(stop)}\nShares:    {shares}\n"
            f"Risk:      {_fmt(acct*rp/100)} ({rp:.1f}% of account)\n\n"
            f"TARGETS:\n3R:  {_fmt(t3)}  ← trim 20%\n5R:  {_fmt(t5)}  ← trim 30%\n"
            f"Max: {_fmt(full_t)}  ← exit rest\nTrail: 10 EMA daily close",
            language="text")

    if st.session_state.get("tk_placing"):
        with st.form("tk_place_form"):
            st.markdown("**Confirm placed order**")
            fe = st.number_input("Actual entry price", min_value=0.0, value=float(entry or 0),
                                 step=0.01, format="%.2f")
            fsh = st.number_input("Shares bought", min_value=0, value=int(shares), step=1)
            if st.form_submit_button("Confirm", type="primary"):
                from datetime import date as _date
                store.add_position({
                    "ticker": tk, "entry": round(float(fe), 2), "stop": round(float(stop), 2),
                    "shares": int(fsh), "target_3r": round(t3, 2) if t3 else None,
                    "target_5r": round(t5, 2) if t5 else None, "trail_mode": "EMA10",
                    "setup": _active.get("pattern", ""), "score": _active.get("score"),
                    "rs": _active.get("rs"), "entry_date": _date.today().isoformat(),
                    "status": "OPEN"})
                for k in ("active_trade", "tk_placing", "tk_show_copy"):
                    st.session_state.pop(k, None)
                st.session_state["_pos_toast"] = f"✅ {tk} added to open positions"
                st.rerun()
    st.divider()

_pt = st.session_state.pop("_pos_toast", None)
if _pt:
    st.toast(_pt, icon="✅")


# =====================================================================
# MODE B (rendered first so a lookup can pre-fill the manual inputs below)
# =====================================================================
st.markdown("<div class='pp-section'>🔍 Look up a live setup</div>", unsafe_allow_html=True)
lc1, lc2 = st.columns([3, 1], vertical_alignment="bottom")
with lc1:
    tk_in = st.text_input("Ticker", placeholder="NVDA", key="ps_ticker",
                          label_visibility="collapsed")
with lc2:
    do_lookup = st.button("🔍 Analyse", key="ps_lookup_btn", use_container_width=True)

if do_lookup and tk_in.strip():
    tk = tk_in.strip().upper()
    with st.spinner(f"Fetching {tk} and detecting pattern…"):
        res = ohlcv_mod.fetch_daily(tk, cache_only=False)     # fresh data (constraint 7)
        if res.empty or len(res.df) < 30:
            st.session_state["ps_lookup"] = {"ticker": tk, "error": "no price data available"}
        else:
            d = ohlcv_mod.add_moving_averages(res.df)
            last = d.iloc[-1]
            pat = patterns_mod.best_pattern(d, finviz_signals=None, require_measured=True)
            info = {"ticker": tk, "price": float(last["Close"]),
                    "ema10": float(last.get("EMA10", float("nan"))),
                    "ema20": float(last.get("EMA20", float("nan")))}
            if pat is not None and pat.measured_target is not None:
                setup = entries_mod.compute_setup(pat.trigger, pat.support_low, pat.measured_target)
                info.update({
                    "pattern": pat.label, "confidence": pat.confidence,
                    "finviz_confirmed": bool(getattr(pat, "finviz_confirmed", False)),
                    "trigger": pat.trigger, "entry": setup.entry, "stop": setup.stop,
                    "stop_kind": setup.stop_kind, "reward_risk": setup.reward_risk,
                    "rr_ok": setup.rr_ok, "target_3r": setup.target_3r,
                    "target_5r": setup.target_5r, "measured_target": setup.measured_target})
                # pre-fill the manual calculator with the engine's entry/stop
                st.session_state["ps_entry"] = round(float(setup.entry), 2)
                st.session_state["ps_stop"] = round(float(setup.stop), 2)
            st.session_state["ps_lookup"] = info
    st.rerun()

lk = st.session_state.get("ps_lookup")
if lk:
    if lk.get("error"):
        st.markdown(f"<div class='pp-empty'>{c._html.escape(lk['ticker'])}: {lk['error']}</div>",
                    unsafe_allow_html=True)
    elif "entry" not in lk:
        st.markdown(
            f"<div class='pp-ps-card'><b>{c._html.escape(lk['ticker'])}</b> — "
            f"current {_fmt(lk['price'])} · 10 EMA {_fmt(lk['ema10'])} · 20 EMA {_fmt(lk['ema20'])}"
            f"<div class='pp-sub'>No valid pattern detected — enter entry/stop manually below.</div>"
            f"</div>", unsafe_allow_html=True)
    else:
        acct = st.session_state["ps_account"]; rp = st.session_state["ps_risk"]
        shares = sizing_mod.compute_shares(acct, rp, lk["entry"], lk["stop"])
        risk_ps = lk["entry"] - lk["stop"]
        posval = shares * lk["entry"]
        rr_ok = lk.get("rr_ok")
        fv = " · ✅ Finviz confirmed" if lk.get("finviz_confirmed") else ""
        rr = lk.get("reward_risk")
        rr_txt = (f"{rr:.1f}:1 " + ("✅ Pinpoint pass" if rr_ok else "⚠ below 5:1"))if rr else "—"
        st.markdown(
            f"<div class='pp-ps-card'>"
            f"<div class='pp-ps-h'>{c._html.escape(lk['ticker'])} — {c._html.escape(lk.get('pattern',''))}</div>"
            f"<div class='pp-sub'>Confidence {lk.get('confidence',0):.2f}{fv} · "
            f"current {_fmt(lk['price'])} · trigger {_fmt(lk.get('trigger'))}</div>"
            f"<div class='pp-ps-grid'>"
            f"<span>Entry</span><b>{_fmt(lk['entry'])}</b>"
            f"<span>Stop (.89 {c._html.escape(str(lk.get('stop_kind','')))})</span><b class='red'>{_fmt(lk['stop'])}</b>"
            f"<span>Risk/share</span><b class='red'>{_fmt(risk_ps)}</b>"
            f"<span>Shares</span><b>{shares:,}</b>"
            f"<span>Position value</span><b>{_fmt(posval)}</b>"
            f"<span>3R target</span><b class='green'>{_fmt(lk.get('target_3r'))}</b>"
            f"<span>5R target ← aim</span><b class='green'>{_fmt(lk.get('target_5r'))}</b>"
            f"<span>R:R</span><b>{rr_txt}</b>"
            f"</div></div>", unsafe_allow_html=True)
        b1, b2 = st.columns(2)
        with b1:
            if st.button("⭐ Add to Watchlist", key="ps_add_wl",
                         disabled=not rr_ok, use_container_width=True):
                store.add_to_watchlist(lk["ticker"])
                for k in ("_wl_members", "wlpage_key"):
                    st.session_state.pop(k, None)
                st.toast(f"Added {lk['ticker']} to watchlist")
        with b2:
            st.caption("Trade details (copy):")
        st.code(
            f"{lk['ticker']}  {lk.get('pattern','')}\n"
            f"Entry  {lk['entry']:.2f}\nStop   {lk['stop']:.2f}  (.89 rule)\n"
            f"Shares {shares}  (pos ${posval:,.0f})\n"
            f"3R {lk.get('target_3r',0):.2f} | 5R {lk.get('target_5r',0):.2f} | "
            f"R:R {rr:.1f}:1" if rr else "", language="text")

st.divider()

# =====================================================================
# MODE A — MANUAL CALCULATOR (instant, offline)
# =====================================================================
st.markdown("<div class='pp-section'>📐 Manual calculator</div>", unsafe_allow_html=True)
i1, i2 = st.columns(2)
with i1:
    account = st.number_input("Account size ($)", min_value=0.0, step=1000.0, format="%.0f",
                              key="ps_account")
    entry = st.number_input("Entry price ($)", min_value=0.0, step=0.01, format="%.2f",
                            key="ps_entry")
with i2:
    risk_pct = st.slider("Risk per trade (%)", min_value=0.1, max_value=2.0, step=0.1,
                         key="ps_risk")
    stop = st.number_input("Stop price ($)", min_value=0.0, step=0.01, format="%.2f",
                           key="ps_stop")

# persist account/risk to the shared store when they change (syncs with Settings)
if (float(account) != float(_us.get("account", 0) or 0)
        or float(risk_pct) != float(_us.get("risk_pct", 0) or 0)):
    merged = dict(_us); merged.update({"account": float(account), "risk_pct": float(risk_pct)})
    store.save_user_settings(merged)

# ---- calculations (instant) ----
dollar_risk = account * risk_pct / 100.0
risk_ps = entry - stop if (entry and stop) else float("nan")
valid = entry > 0 and stop > 0 and risk_ps == risk_ps and risk_ps > 0
shares = sizing_mod.compute_shares(account, risk_pct, entry, stop) if valid else 0
posval = shares * entry
pct_acct = (posval / account * 100.0) if account else 0.0

if not valid:
    st.markdown("<div class='pp-sub'>Enter an entry above a stop to size the position.</div>",
                unsafe_allow_html=True)
else:
    targets = {n: entry + n * risk_ps for n in (1, 2, 3, 5, 10)}
    sr = entries_mod.liquidity_stop(stop)           # .89 rule on the entered stop (constraint 1)
    st.markdown(
        f"<div class='pp-ps-card'>"
        f"<div class='pp-ps-grid'>"
        f"<span>Dollar risk</span><b class='red'>{_fmt(dollar_risk)}</b>"
        f"<span>Risk per share</span><b class='red'>{_fmt(risk_ps)}</b>"
        f"<span>Shares to buy</span><b class='big'>{shares:,}</b>"
        f"<span>Position value</span><b class='big'>{_fmt(posval)}</b>"
        f"<span>% of account</span><b>{pct_acct:.1f}%</b>"
        f"</div>"
        f"<div class='pp-ps-sub'>── Targets ──</div>"
        f"<div class='pp-ps-grid'>"
        f"<span>1R</span><b class='green'>{_fmt(targets[1])}</b>"
        f"<span>2R</span><b class='green'>{_fmt(targets[2])}</b>"
        f"<span>3R ← trim</span><b class='green'>{_fmt(targets[3])}</b>"
        f"<span>5R ← PDF target</span><b class='green'>{_fmt(targets[5])}</b>"
        f"<span>10R</span><b class='green'>{_fmt(targets[10])}</b>"
        f"</div>"
        f"<div class='pp-ps-sub'>── Liquidity stop (.89 rule) ──</div>"
        f"<div class='pp-ps-grid'>"
        f"<span>Suggested stop</span><b class='red'>{_fmt(sr.stop)}</b>"
        f"<span>cluster</span><b>{_fmt(sr.cluster_level)} ({c._html.escape(sr.cluster_kind)})</b>"
        f"</div></div>", unsafe_allow_html=True)

    # ---- warnings ----
    if pct_acct > 25:
        st.warning(f"Concentrated position — {pct_acct:.0f}% of account (> 25%).")
    if risk_ps < 0.50:
        st.warning(f"Stop too tight — risk/share is only {_fmt(risk_ps)} (< $0.50).")

c.disclaimer_footer()
