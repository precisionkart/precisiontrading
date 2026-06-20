"""Dashboard view — redesigned (Phase 11 UI refresh).

Layout: KPI strip → [sector strength ladder | open-positions widget] →
ranked setups as compact RS/Score-forward cards → overnight earnings reactions.
All presentation lives in dashboard_ui (ppx- design system); the data model and
behaviour are unchanged.
"""

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as c
import dashboard_logic as dl
import dashboard_ui as ui
from pinpoint import store, analyzer
from pinpoint import themes as themes_mod

CLOUD = c.cloud_mode()
scan = st.session_state.get("scan")

ui.inject_css()
c.page_header("Today")
c.weekend_banner()

# Positions are computed once (cache-only, fast) and shared by the KPI strip and
# the widget so we never enrich them twice.
positions = store.open_positions()
lives = [c._position_live(p) for p in positions]
open_r = sum(lv["r_mult"] for lv in lives if lv.get("r_mult") is not None)

if not scan:
    ui.positions_widget(lives)
    if CLOUD:
        msg = "No published scan found (data/latest_scan.json)."
    else:
        nxt = c.next_scheduled_scan().strftime("%a %-d %b, 09:30")
        msg = (f"No scan today — next scheduled scan: {nxt} ET. "
               "Or hit the ↻ refresh in the sidebar to run one now.")
    st.markdown(f"<div class='pp-sub'>{msg}</div>", unsafe_allow_html=True)
    c.disclaimer_footer()
    st.stop()

theme_ctx = scan.get("theme_ctx") or themes_mod.context_from_list(scan.get("themes", []))
sector_filter = st.session_state.get("sector_filter")

# Stale-data banner: if the loaded scan isn't from today, say so plainly.
_lbl, _col = c.refresh_status(scan)
if _col != "#00D964":
    nxt = c.next_scheduled_scan().strftime("%a %-d %b, 09:30")
    st.markdown(
        f"<div class='pp-stale'>⚠ Showing the last scan ({c._html.escape(_lbl)}) — "
        f"not from today. Hit ↻ in the sidebar to run a fresh one, or wait for the "
        f"next scheduled scan: {nxt} ET.</div>", unsafe_allow_html=True)
c.warning_banner(scan.get("warnings"))

focus = scan.get("focus")
ef_tickers = set()
if focus is not None and len(focus) and "earnings_flag_active" in focus.columns:
    ef_tickers = set(focus[focus["earnings_flag_active"] == True]["ticker"].astype(str))  # noqa: E712


def detail_fn(tk: str):
    cache = st.session_state.setdefault("detail_cache", {})
    key = f"{tk}:{'c' if CLOUD else 'l'}"
    if key not in cache:
        ref, _ = c.reference_universe()
        with st.spinner(f"Analyzing {tk}…"):
            res = analyzer.analyze_picks(
                None if CLOUD else c.get_client(), [tk], scan["regime"],
                reference_universe=ref, theme_ctx=theme_ctx,
                offline_universe=ref if CLOUD else None, cache_only=CLOUD)
        cache[key] = res[0] if res else None
    return cache[key]


# ---- Tiered output ----
tiers = dl.build_tiers(scan.get("focus"), scan.get("targets"), sector_filter)
t1 = tiers[tiers["Tier"] == "Elite"]
t2 = tiers[tiers["Tier"] == "Good"]
t3 = tiers[tiers["Tier"] == "Watchlist"]
_tgt = scan.get("targets")
scanned = max(len(_tgt) if _tgt is not None else 0, len(tiers))
regime_state = getattr(scan.get("regime"), "state", "neutral")

# ---- KPI strip ----
ui.kpi_strip(regime_state, n_setups=len(tiers), n_open=len(lives), open_r=open_r)

# Split the Elite tier: the #1 setup becomes the "focus" card up top (beside the
# positions widget, like the mockup); the rest fall into the ranked list below.
t1_rows = list(t1.iterrows())
focus_row = t1_rows[0] if t1_rows else None
rest_t1 = t1_rows[1:]

# ---- Today's focus + open positions, side by side (mockup grid-a) ----
col_l, col_r = st.columns([1.62, 1], gap="medium")
with col_l:
    if focus_row is not None:
        _, frow = focus_row
        ui.setup_card(frow.to_dict(), detail_fn, key="focus0",
                      ef=str(frow.get("Ticker")) in ef_tickers, tier=1, focus=True)
    else:
        st.markdown("<div class='pp-empty'>No Elite setup today.</div>",
                    unsafe_allow_html=True)
with col_r:
    ui.positions_widget(lives)

# ---- Sector strength + the rest of the setups, side by side (mockup grid-b) ----
col_sl, col_sr = st.columns([1, 1.62], gap="medium")
with col_sl:
    ui.sector_ladder(scan.get("themes", []))
with col_sr:
    # Build the ranked rows for the table: rest of Elite + all Good (the focus
    # stock is already shown as the big card above, so it's excluded here).
    table_rows = [row.to_dict() for _, row in rest_t1]
    if len(t2):
        table_rows += [row.to_dict() for _, row in t2.iterrows()]

    def _pick(tk):
        st.session_state["mp_prefill"] = tk
        st.switch_page("views/my_picks.py")

    ui.setups_table(table_rows, _pick, n_total=scanned, key="setups")
if len(t3):
    st.markdown("<div class='ppx-h' style='font-size:14px;margin-top:18px'>Watchlist (50-64)"
                "</div>", unsafe_allow_html=True)
    _t3 = list(t3.iterrows())
    per_row = 6
    for _r0 in range(0, len(_t3), per_row):
        chunk = _t3[_r0:_r0 + per_row]
        cols = st.columns(per_row)
        for _ci3, (_, r) in enumerate(chunk):
            tkp = str(r.get("Ticker"))
            with cols[_ci3]:
                if st.button(f"{tkp} {r.get('Score'):.0f}", key=f"wp_{tkp}",
                             help=f"Analyse {tkp}", use_container_width=True):
                    st.session_state["mp_prefill"] = tkp
                    st.switch_page("views/my_picks.py")
c.scroll_to_card()

# ---- Overnight earnings reactions ----
universe_tks = set()
_t = scan.get("targets")
if _t is not None and len(_t) and "ticker" in _t.columns:
    universe_tks = set(_t["ticker"].astype(str))
st.markdown("<div class='ppx-h'>Overnight earnings reactions</div>", unsafe_allow_html=True)
ecol1, ecol2 = st.columns(2)
with ecol1:
    c.earnings_panel(scan.get("earnings"), "📈 Gapping Up", "up", universe_tks)
with ecol2:
    c.earnings_panel(scan.get("earnings_down"), "📉 Gapping Down (AVOID)", "down", universe_tks)

c.disclaimer_footer()