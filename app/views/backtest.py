"""Backtest view — walk-forward results for the Pinpoint strategy.

Renders the most recent backtest in data/backtest/ (or runs a fresh one on
demand): summary cards, equity curve, by-pattern / by-regime tables, exit-reason
breakdown, and the full sortable trade log with a CSV download.
"""

import glob
import json
import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as c
from pinpoint.config import CONFIG
from pinpoint.backtest import BacktestEngine, BACKTEST_UNIVERSE

c.page_header("Backtest")
st.markdown("<div class='pp-sub'>Walk-forward simulation of the Pinpoint setup engine "
            "— point-in-time scans (zero lookahead), .89 stops, 5R targets, 10-EMA "
            "close trail. Research only.</div>", unsafe_allow_html=True)

_BT_DIR = os.path.join(CONFIG.paths.data_dir, "backtest")


def _latest_results() -> dict | None:
    files = sorted(glob.glob(os.path.join(_BT_DIR, "results_*.json")),
                   key=os.path.getmtime, reverse=True)
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                r = json.load(f)
            r["_path"] = fp
            r["_trades_csv"] = fp.replace("results_", "trades_").replace(".json", ".csv")
            return r
        except Exception:  # noqa: BLE001
            continue
    return None


# ---- Run controls ----
rc1, rc2, rc3 = st.columns([2, 2, 3], vertical_alignment="bottom")
with rc1:
    start = st.text_input("Start", value="2025-12-01", key="bt_start")
with rc2:
    end = st.text_input("End", value="2026-03-01", key="bt_end")
with rc3:
    if st.button("▶ Run Backtest", type="primary", key="bt_run"):
        with st.spinner(f"Running walk-forward backtest {start} → {end} "
                        f"({len(BACKTEST_UNIVERSE)} tickers)…"):
            try:
                eng = BacktestEngine(start_date=start, end_date=end)
                st.session_state["backtest_results"] = eng.run()
                st.session_state["backtest_ran_at"] = c.now_hhmm()
                st.toast("Backtest complete", icon="✅")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Backtest failed: {exc}")
        st.rerun()

results = st.session_state.get("backtest_results") or _latest_results()
if not results:
    st.markdown("<div class='pp-empty'>No backtest yet. Hit ▶ Run Backtest above "
                "(or run <code>python backtest_run.py</code>).</div>", unsafe_allow_html=True)
    c.disclaimer_footer()
    st.stop()

ran_at = st.session_state.get("backtest_ran_at")
p = results.get("params", {})
st.markdown(f"<div class='pp-section'>{p.get('start','?')} → {p.get('end','?')} · "
            f"{p.get('universe_size','?')} tickers · ${p.get('account_size',0):,.0f} @ "
            f"{p.get('risk_pct',0)*100:.1f}% risk"
            f"{' · last run ' + ran_at if ran_at else ''}</div>", unsafe_allow_html=True)


def _color(v, good_when_positive=True, thresh=0.0):
    ok = (v > thresh) if good_when_positive else (v < thresh)
    return "#16A34A" if ok else "#DC2626"


# ---- 1. Summary cards ----
m1, m2, m3, m4 = st.columns(4)
wr = results["win_rate_pct"]
exp = results["expectancy"]
ret = results["total_return_pct"]
dd = results["max_drawdown_pct"]
for col, label, val, color in (
    (m1, "Win Rate", f"{wr:.1f}%", _color(wr - 50)),
    (m2, "Expectancy", f"{exp:.2f}R", _color(exp)),
    (m3, "Total Return", f"{ret:+.1f}%", _color(ret)),
    (m4, "Max Drawdown", f"{dd:.1f}%", "#DC2626"),
):
    col.markdown(
        f"<div class='pp-bt-card'><div class='pp-bt-lbl'>{label}</div>"
        f"<div class='pp-bt-val' style='color:{color}'>{val}</div></div>",
        unsafe_allow_html=True)

st.markdown(
    f"<div class='pp-tiercount'>Signals {results['total_signals']} · Trades "
    f"{results['total_trades']} (fill {results['fill_rate_pct']:.0f}%) · "
    f"Winners {results['total_winners']} / Losers {results['total_losers']} · "
    f"Avg R {results['avg_r_all']:.2f} · Final ${results['final_equity']:,.0f}</div>",
    unsafe_allow_html=True)

# ---- 2. Equity curve ----
eq = results.get("daily_equity") or []
if eq:
    st.markdown("<div class='pp-section'>Equity curve</div>", unsafe_allow_html=True)
    eqdf = pd.DataFrame(eq)
    eqdf["date"] = pd.to_datetime(eqdf["date"])
    eqdf = eqdf.set_index("date")
    eqdf["baseline"] = p.get("account_size", 100_000)
    st.line_chart(eqdf[["equity", "baseline"]], height=260)

# ---- 3. By pattern ----
def _stats_df(grp: dict, key_label: str) -> pd.DataFrame:
    rows = [{key_label: k, "Trades": v["count"], "Win Rate": v["win_rate"],
             "Avg R": v["avg_r"], "Best R": v["best_r"]}
            for k, v in grp.items() if v["count"] > 0]
    df = pd.DataFrame(rows)
    return df.sort_values("Avg R", ascending=False).reset_index(drop=True) if len(df) else df


def _show_stats(df: pd.DataFrame):
    if len(df) == 0:
        st.markdown("<div class='pp-sub'>No trades.</div>", unsafe_allow_html=True)
        return
    sty = df.style.map(lambda v: f"color:{'#16A34A' if v >= 50 else '#DC2626'}",
                       subset=["Win Rate"]).format(
        {"Win Rate": "{:.0f}%", "Avg R": "{:.2f}", "Best R": "{:.2f}"})
    st.dataframe(sty, use_container_width=True, hide_index=True)


cpat, creg = st.columns(2)
with cpat:
    st.markdown("<div class='pp-section'>By pattern</div>", unsafe_allow_html=True)
    _show_stats(_stats_df(results.get("by_pattern", {}), "Pattern"))
with creg:
    st.markdown("<div class='pp-section'>By regime</div>", unsafe_allow_html=True)
    _show_stats(_stats_df(results.get("by_regime", {}), "Regime"))

# ---- Exit reasons + top tickers ----
cex, ctk = st.columns(2)
with cex:
    st.markdown("<div class='pp-section'>Exit reasons</div>", unsafe_allow_html=True)
    er = results.get("exit_reasons", {})
    erdf = pd.DataFrame([{"Reason": k, "Count": v["count"], "%": v["pct"]}
                        for k, v in er.items() if v["count"] > 0])
    if len(erdf):
        st.dataframe(erdf, use_container_width=True, hide_index=True)
    else:
        st.markdown("<div class='pp-sub'>—</div>", unsafe_allow_html=True)
with ctk:
    st.markdown("<div class='pp-section'>Top tickers by R</div>", unsafe_allow_html=True)
    tt = results.get("top_tickers", [])[:10]
    if tt:
        st.dataframe(pd.DataFrame(tt).rename(columns={
            "ticker": "Ticker", "total_r": "Total R", "trades": "Trades",
            "win_rate": "Win %"}), use_container_width=True, hide_index=True)
    else:
        st.markdown("<div class='pp-sub'>—</div>", unsafe_allow_html=True)

# ---- 5. Full trade log ----
csv_path = results.get("_trades_csv", "")
with st.expander(f"Full trade log ({results['total_trades']} trades)", expanded=False):
    if csv_path and os.path.exists(csv_path):
        tdf = pd.read_csv(csv_path)
        cols = ["entry_date", "ticker", "pattern", "entry", "stop", "exit_price",
                "r_multiple", "win", "days_held", "exit_reason", "regime"]
        view = tdf[[col for col in cols if col in tdf.columns]].rename(columns={
            "entry_date": "Date", "ticker": "Ticker", "pattern": "Pattern",
            "entry": "Entry", "stop": "Stop", "exit_price": "Exit",
            "r_multiple": "R", "win": "Win", "days_held": "Days",
            "exit_reason": "Exit reason", "regime": "Regime"})
        sty = view.style.apply(
            lambda row: ["background-color:rgba(22,163,74,0.10)" if row.get("Win")
                         else "background-color:rgba(220,38,38,0.08)"] * len(row), axis=1)
        st.dataframe(sty, use_container_width=True, hide_index=True)
        st.download_button("⬇ Download trades CSV", tdf.to_csv(index=False),
                           file_name=os.path.basename(csv_path), mime="text/csv")
    else:
        st.markdown("<div class='pp-sub'>Trade CSV not found — re-run the backtest.</div>",
                    unsafe_allow_html=True)

c.disclaimer_footer()
