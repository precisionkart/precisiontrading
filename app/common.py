"""app/common.py — shared helpers for the Streamlit pages.

Bootstraps the page (CSS, fonts), provides a cached Finviz client, the full-scan
runner (which persists to store.py), and the HTML card renderers shared by the
Morning Dashboard and My Picks. No strategy logic lives here — it calls the same
pinpoint/ engine the CLI uses.
"""

from __future__ import annotations

import base64
import html as _html
import os
import sys
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import streamlit as st

# make the repo importable when run via `streamlit run app/Home.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pinpoint import __version__, DISCLAIMER          # noqa: E402
from pinpoint import pipeline, store                  # noqa: E402
from pinpoint import regime as regime_mod             # noqa: E402
from pinpoint import themes as themes_mod             # noqa: E402
from pinpoint import ipo as ipo_mod                   # noqa: E402
from pinpoint import ohlcv as ohlcv_mod               # noqa: E402
from pinpoint import charts                           # noqa: E402
from pinpoint.config import CONFIG                    # noqa: E402
from pinpoint.finviz_client import FinvizClient       # noqa: E402

_REGIME_COLOR = {"bull": "#16A34A", "neutral": "#737373", "bear": "#DC2626"}
_CSS_PATH = os.path.join(os.path.dirname(__file__), "style.css")


@dataclass
class RegimeView:
    state: str
    rationale: list


# ---------------------------------------------------------------------------
# Bootstrap.
# ---------------------------------------------------------------------------
def bootstrap(title: str) -> None:
    st.set_page_config(page_title=f"Pinpoint — {title}", layout="centered",
                       initial_sidebar_state="expanded")
    with open(_CSS_PATH, encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


@st.cache_resource
def get_client() -> FinvizClient:
    return FinvizClient()


def now_hhmm() -> str:
    return datetime.now().strftime("%H:%M")


# ---------------------------------------------------------------------------
# Header + regime.
# ---------------------------------------------------------------------------
def header(title_dim: str, as_of: str) -> None:
    st.markdown(
        f"<div style='display:flex;align-items:baseline;justify-content:space-between'>"
        f"<div class='pp-h1'>Pinpoint <span class='dim'>— {title_dim}</span></div>"
        f"<div class='pp-asof'>Data as of {_html.escape(as_of or '—')}</div></div>",
        unsafe_allow_html=True)


def regime_line(state: str, rationale: list) -> None:
    color = _REGIME_COLOR.get(state, "#737373")
    ctx = _html.escape("; ".join(rationale or []))
    st.markdown(
        f"<div class='pp-regime'><span class='pp-dot' style='background:{color}'></span>"
        f"{state.upper()} <span class='ctx'>— {ctx}</span></div>", unsafe_allow_html=True)


def warning_banner(warnings: list) -> None:
    blocked = [w for w in (warnings or []) if "403" in w or "block" in w.lower()]
    if blocked:
        st.warning("Finviz throttled some requests (403/empty) — results may be "
                   "partial. Try again shortly or raise the request delay.")


# ---------------------------------------------------------------------------
# Full scan (persists to store).
# ---------------------------------------------------------------------------
def full_scan(ignore_rvol: bool = False) -> dict:
    client = get_client()
    warnings: list[str] = []

    with st.spinner("Reading market regime (SPY/QQQ)..."):
        reg = regime_mod.fetch_regime(client)
    with st.spinner("Ranking sector/theme ETFs and industry groups..."):
        theme_ctx = themes_mod.build_theme_context(client)
        themes_mod.log_theme_history(theme_ctx)
        warnings += theme_ctx.warnings
    with st.spinner("Tracking recent IPOs vs their initial highs..."):
        ipo_res = ipo_mod.build_ipo_watchlist(client)
        warnings += ipo_res.warnings
    with st.spinner("Fetching Finviz screens and ranking Targets..."):
        uni = pipeline.fetch_targets_universe(client, reg, theme_ctx=theme_ctx,
                                              ipo_ctx=ipo_res.ipo_ctx, ignore_rvol=ignore_rvol)
        warnings += uni.warnings
    with st.spinner("Enriching Focus with OHLCV (patterns, entries, R:R)..."):
        index_daily = ohlcv_mod.fetch_daily(CONFIG.regime.benchmarks[0]).df
        focus = pipeline.enrich_focus(uni.df, uni.universe, reg, index_daily=index_daily,
                                      theme_ctx=theme_ctx, ipo_ctx=ipo_res.ipo_ctx)
    with st.spinner("Scanning overnight earnings reactions..."):
        ern = pipeline.run_earnings(client)
        warnings += ern.warnings

    as_of = now_hhmm()
    # RS reference for My-Picks must be BROAD (no RVOL gate), per the 7B note.
    with st.spinner("Saving a broad RS reference universe..."):
        from pinpoint.config import RS_REFERENCE_SCREEN
        ref = client.fetch_universe(RS_REFERENCE_SCREEN, views=("performance",))
        if not ref.empty:
            store.save_universe_snapshot(pipeline.normalize_universe(ref.df))
    store.save_scan_cache({"focus": focus, "targets": uni.df, "earnings": ern.df,
                           "ipo": ipo_res.watchlist}, reg, as_of,
                          theme_rank=theme_ctx.theme_rank)
    return {"regime": RegimeView(reg.state, reg.rationale), "theme_ctx": theme_ctx,
            "themes": store._themes_list(theme_ctx.theme_rank),
            "focus": focus, "targets": uni.df, "earnings": ern.df,
            "ipo": ipo_res.watchlist, "as_of": as_of, "warnings": warnings}


def reference_universe():
    """Reference universe for My-Picks RS: the last full-scan snapshot (or None)."""
    return store.load_universe_snapshot()


# ---------------------------------------------------------------------------
# Read-only cloud mode (Phase 8): render from the committed latest_scan.json,
# make ZERO live Finviz calls.
# ---------------------------------------------------------------------------
def cloud_mode() -> bool:
    return os.environ.get("PINPOINT_CLOUD", "").lower() in ("1", "true", "yes")


def load_published():
    """Build the scan dict from the committed latest_scan.json (or None)."""
    payload = store.load_published_scan()
    if not payload:
        return None

    def _df(name):
        return pd.DataFrame(payload.get("lists", {}).get(name, []))

    reg = payload.get("regime", {})
    return {"regime": RegimeView(reg.get("state", "neutral"), reg.get("rationale", [])),
            "theme_ctx": None, "themes": payload.get("themes", []),
            "focus": _df("focus"), "targets": _df("targets"),
            "earnings": _df("earnings"), "ipo": _df("ipo"),
            "as_of": payload.get("as_of_et", ""), "date": payload.get("date", ""),
            "warnings": []}


# ---------------------------------------------------------------------------
# Card rendering.
# ---------------------------------------------------------------------------
def _chart_uri(ticker: str, daily, ann) -> str:
    png = charts.render_focus_png_bytes(ticker, daily, ann)
    if not png:
        return ""
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _chips_html(layers: str) -> str:
    if not isinstance(layers, str) or not layers or layers.startswith("DISQUAL"):
        return ""
    parts = [p.strip() for p in layers.split("|") if p.strip()]
    return "".join(f"<span class='pp-chip'>{_html.escape(p)}</span>" for p in parts)


def _fmt(v, nd=2):
    if v is None:
        return "—"
    if isinstance(v, float):
        return "—" if v != v else f"{v:,.{nd}f}"
    return str(v)


def focus_card(row: pd.Series, daily=None, pill: str = "") -> None:
    """Render a Focus-style card (header + chips + chart + Entry/Stop/Target/R:R)."""
    tk = row.get("ticker")
    if daily is None:
        daily = ohlcv_mod.fetch_daily(tk).df
    ann = charts.ChartAnnotation(
        entry=row.get("entry_trigger") if row.get("entry_trigger") == row.get("entry_trigger") else row.get("entry"),
        stop=row.get("stop"), target=row.get("measured_target") if "measured_target" in row else row.get("target"),
        reward_risk=row.get("reward_risk"),
        pattern_label=str(row.get("pattern") or "").split(" /")[0].split(" (")[0] or None,
        pattern_bars=int(row.get("pattern_bars", 0) or 0),
        score=row.get("pinpoint_score") if "pinpoint_score" in row else row.get("score"),
        rs=row.get("rs"))
    uri = _chart_uri(tk, daily, ann) if daily is not None and len(daily) else ""

    sub_bits = [b for b in [row.get("sector"), row.get("theme"), row.get("pattern")] if b]
    sub = " · ".join(str(b) for b in sub_bits)
    score = row.get("pinpoint_score") if "pinpoint_score" in row else row.get("score")
    entry = row.get("entry_trigger") if "entry_trigger" in row else row.get("entry")
    target = row.get("measured_target") if "measured_target" in row else row.get("target")
    pill_html = f"<span class='pp-pill {pill}'>{_pill_label(pill, row.get('reward_risk'))}</span>" if pill else ""

    st.markdown(f"""
<div class='pp-card'>
  <div class='pp-card-head'>
    <div><span class='pp-tk'>{_html.escape(str(tk))}</span><span class='pp-sub'>{_html.escape(sub)}</span> {pill_html}</div>
    <div class='pp-score'>{_fmt(score,1)}<span class='rs'>RS {_fmt(row.get('rs'),0)}</span></div>
  </div>
  <div class='pp-chips'>{_chips_html(row.get('layers',''))}</div>
  {f"<img src='{uri}'>" if uri else "<div class='pp-empty'>chart unavailable</div>"}
  <div class='pp-grid4'>
    <div class='pp-cell'><div class='k'>Entry</div><div class='v green'>${_fmt(entry)}</div></div>
    <div class='pp-cell'><div class='k'>Stop (.89)</div><div class='v red'>${_fmt(row.get('stop'))}</div></div>
    <div class='pp-cell'><div class='k'>Target</div><div class='v'>${_fmt(target)}</div></div>
    <div class='pp-cell'><div class='k'>R:R</div><div class='v'>{_fmt(row.get('reward_risk'),1)}:1</div></div>
  </div>
</div>""", unsafe_allow_html=True)


def _pill_label(pill: str, rr) -> str:
    if pill == "ok":
        return "✓ Pinpoint setup"
    if pill == "near":
        rr_txt = f" (R:R {rr:.1f}:1)" if isinstance(rr, (int, float)) and rr == rr else ""
        return f"△ Setup not ready{rr_txt}"
    if pill == "fail":
        return "✗ Not a setup"
    return ""


def pick_card(pr) -> None:
    """Render a My-Picks card: A+ -> full Focus card; near -> card + amber pill;
    fail -> chart + a per-gate pass/fail checklist + plain-English verdict;
    unavailable -> a small empty-state card (read-only cloud, ticker not cached)."""
    if pr.classification == "unavailable":
        st.markdown(f"""
<div class='pp-card'>
  <div class='pp-card-head'>
    <div><span class='pp-tk'>{_html.escape(pr.ticker)}</span>
      <span class='pp-sub'>not in latest scan</span></div></div>
  <div class='pp-verdict'>{_html.escape(pr.verdict)}</div>
</div>""", unsafe_allow_html=True)
        return

    pill = {"A+": "ok", "near": "near", "fail": "fail"}.get(pr.classification, "fail")

    if pr.classification in ("A+", "near"):
        row = pd.Series({
            "ticker": pr.ticker, "sector": pr.sector, "theme": pr.theme,
            "pattern": pr.pattern, "score": pr.score, "rs": pr.rs,
            "entry": pr.entry, "stop": pr.stop, "target": pr.target,
            "reward_risk": pr.reward_risk, "pattern_bars": pr.pattern_bars,
            "layers": pr.layers})
        focus_card(row, daily=pr.daily, pill=pill)
        return

    # fail card: chart + checklist + verdict
    ann = charts.ChartAnnotation(
        pattern_label=(pr.pattern.split(" /")[0] if pr.pattern else None),
        pattern_bars=pr.pattern_bars, score=pr.score, rs=pr.rs)
    uri = _chart_uri(pr.ticker, pr.daily, ann) if pr.daily is not None else ""
    checks = "".join(
        f"<div class='pp-check'><span><span class='mark {'pass' if c.passed else 'x'}'>"
        f"{'✓' if c.passed else '✗'}</span>&nbsp; {_html.escape(c.name)}</span>"
        f"<span class='detail'>{_html.escape(c.detail)}</span></div>" for c in pr.gates)
    sub = " · ".join(str(b) for b in [pr.sector, pr.theme, pr.stage] if b)
    st.markdown(f"""
<div class='pp-card'>
  <div class='pp-card-head'>
    <div><span class='pp-tk'>{_html.escape(pr.ticker)}</span><span class='pp-sub'>{_html.escape(sub)}</span>
      <span class='pp-pill fail'>✗ Not a setup</span></div>
    <div class='pp-score'>{_fmt(pr.score,1)}<span class='rs'>RS {_fmt(pr.rs,0)}</span></div>
  </div>
  {f"<img src='{uri}'>" if uri else "<div class='pp-empty'>chart unavailable</div>"}
  <div class='pp-checks'>{checks}</div>
  <div class='pp-verdict'>{_html.escape(pr.verdict)}</div>
</div>""", unsafe_allow_html=True)


def render_top10(df, key: str = "top10") -> Optional[str]:
    """Render the Top-10 table (RS cells tier-colored, Score gradient) with
    single-row selection. Returns the selected ticker, or None."""
    import dashboard_logic as dl
    if df is None or len(df) == 0:
        st.markdown("<div class='pp-empty'>No ranked names yet.</div>", unsafe_allow_html=True)
        return None

    def _rs_style(v):
        try:
            bg, fg = dl.rs_tier(float(v))
        except (TypeError, ValueError):
            return ""
        return f"background-color:{bg};color:{fg};font-weight:600"

    styler = (df.style
              .map(_rs_style, subset=["RS"])
              .background_gradient(subset=["Score"], cmap="Greens")
              .format({"Score": lambda v: f"{v:.1f}" if pd.notna(v) else "—",
                       "RS": lambda v: f"{v:.0f}" if pd.notna(v) else "—",
                       "R:R": lambda v: f"{v:.1f}:1" if pd.notna(v) else "—"}))
    event = st.dataframe(styler, use_container_width=True, hide_index=True,
                         on_select="rerun", selection_mode="single-row", key=key)
    rows = []
    try:
        rows = event.selection.rows
    except Exception:  # noqa: BLE001
        pass
    if rows:
        return str(df.iloc[rows[0]]["Ticker"])
    return None


def render_sector_strength(rows, key: str = "sector") -> Optional[str]:
    """Render the Sector Strength widget with single-row selection. Returns the
    clicked sector/theme name, or None."""
    import dashboard_logic as dl
    if not rows:
        st.markdown("<div class='pp-empty'>Theme rankings unavailable.</div>",
                    unsafe_allow_html=True)
        return None
    disp = pd.DataFrame([{"Sector": r["Sector"], "RS Score": r["RS Score"],
                          "Δ vs prior": dl.delta_str(r["Δ"]),
                          "Hot": "● Hot" if r["hot"] else ""} for r in rows])
    styler = (disp.style
              .background_gradient(subset=["RS Score"], cmap="Greens")
              .format({"RS Score": lambda v: f"{v:.1f}" if pd.notna(v) else "—"}))
    event = st.dataframe(styler, use_container_width=True, hide_index=True,
                         on_select="rerun", selection_mode="single-row", key=key)
    rows_sel = []
    try:
        rows_sel = event.selection.rows
    except Exception:  # noqa: BLE001
        pass
    if rows_sel:
        return str(disp.iloc[rows_sel[0]]["Sector"])
    return None


def rs_badge_html(rs) -> str:
    import dashboard_logic as dl
    bg, fg = dl.rs_tier(rs if isinstance(rs, (int, float)) else float("nan"))
    val = "—" if (rs is None or (isinstance(rs, float) and rs != rs)) else f"{rs:.0f}"
    return (f"<div class='pp-rsbadge' style='background:{bg};color:{fg}'>"
            f"<span class='lab'>RS</span><span class='val'>{val}</span></div>")


def pills_html(criteria) -> str:
    import dashboard_logic as dl
    cells = []
    for c in criteria:
        kind = dl.pill_kind(c.passed)
        mark = "✓" if c.passed else "✗"
        val = f"<span class='val'>{_html.escape(str(c.value))}</span>" if c.value else ""
        cells.append(
            f"<div class='pp-pill2 {kind}'><span class='name'><span class='mk'>{mark}</span>"
            f"{_html.escape(c.label)}</span>{val}</div>")
    return "<div class='pp-pills2'>" + "".join(cells) + "</div>"


def stat_row_html(entry, stop, target, rr) -> str:
    return f"""<div class='pp-stats'>
  <div class='pp-stat'><div class='k'>Entry</div><div class='v green'>${_fmt(entry)}</div></div>
  <div class='pp-stat'><div class='k'>Stop (.89)</div><div class='v red'>${_fmt(stop)}</div></div>
  <div class='pp-stat'><div class='k'>Target</div><div class='v'>${_fmt(target)}</div></div>
  <div class='pp-stat'><div class='k'>R : R</div><div class='v'>{_fmt(rr,1)}:1</div></div>
</div>"""


def legend_html(entry, stop, target, rr) -> str:
    from pinpoint import chart_config as cc
    items = []
    for label, price, color in cc.annotation_lines(entry, stop, target, rr):
        items.append(f"<span class='item'><span class='sw' style='border-color:{color}'></span>"
                     f"{_html.escape(label)} <b>${price:.2f}</b></span>")
    return "<div class='pp-legend'>" + "".join(items) + "</div>" if items else ""


def render_detail(pr) -> None:
    """The drill-down detail view: RS badge + ticker, interactive daily/weekly
    Plotly charts (legend below), color-coded pills, Entry/Stop/Target/R:R stats,
    and a template-generated technical-analysis paragraph."""
    import dashboard_logic as dl
    import charts_plotly as cp

    sub = " · ".join(str(b) for b in [pr.sector, pr.theme, pr.pattern or pr.stage] if b)
    st.markdown(f"<div class='pp-detail-head'>{rs_badge_html(pr.rs)}"
                f"<div><div class='tk'>{_html.escape(pr.ticker)}</div>"
                f"<div class='sub'>{_html.escape(sub)}</div></div></div>", unsafe_allow_html=True)

    if pr.daily is not None and len(pr.daily):
        dfig = cp.daily_figure(pr.daily, entry=pr.entry, stop=pr.stop, target=pr.target,
                               pattern_bars=pr.pattern_bars)
        if dfig is not None:
            st.plotly_chart(dfig, use_container_width=True, key=f"d_{pr.ticker}")
        st.markdown(legend_html(pr.entry, pr.stop, pr.target, pr.reward_risk),
                    unsafe_allow_html=True)
        wfig = cp.weekly_figure(pr.daily)
        if wfig is not None:
            st.plotly_chart(wfig, use_container_width=True, key=f"w_{pr.ticker}")
    else:
        st.markdown("<div class='pp-empty'>chart unavailable (OHLCV not cached)</div>",
                    unsafe_allow_html=True)

    if pr.criteria:
        st.markdown(pills_html(pr.criteria), unsafe_allow_html=True)
    if pr.entry is not None or pr.reward_risk is not None:
        st.markdown(stat_row_html(pr.entry, pr.stop, pr.target, pr.reward_risk),
                    unsafe_allow_html=True)
    st.markdown(f"<div class='pp-ta'>{_html.escape(dl.technical_analysis(pr))}</div>",
                unsafe_allow_html=True)


def disclaimer_footer() -> None:
    st.markdown(
        f"<div style='color:#737373;font-size:11px;border-top:1px solid #E5E5E5;"
        f"margin-top:40px;padding-top:14px'>{_html.escape(DISCLAIMER)} · v{__version__}. "
        f"RS is a labeled proxy, not IBD's. Continuity approximated from weekly+daily."
        f"</div>", unsafe_allow_html=True)
