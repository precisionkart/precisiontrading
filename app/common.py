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
from pinpoint import position_sizing as sizing_mod    # noqa: E402
from pinpoint import flags as flags_mod               # noqa: E402
from pinpoint.config import CONFIG                    # noqa: E402
from pinpoint.finviz_client import FinvizClient       # noqa: E402

_REGIME_COLOR = {"bull": "#16A34A", "neutral-bull": "#16A34A", "neutral": "#737373",
                 "neutral-bear": "#CA8A04", "bear": "#DC2626", "very-bear": "#111111"}


def regime_exposure(state: str) -> str:
    """Suggested portfolio exposure label for a regime state (Phase 10 step 7)."""
    from pinpoint.regime import EXPOSURE
    return EXPOSURE.get(state, (0.33, "One-Third"))[1]


# Synthetic dev-only tickers (from tools/seed_earnings_flag_demo.py) that must
# never appear in a live view unless PINPOINT_DEMO_SEED=1 is explicitly set.
SYNTHETIC_TICKERS = {"FLAGX", "ELITEX", "WATCHX", "BADX", "DUMPX"}


def quarantine_synthetic(scan: dict) -> dict:
    """Strip known synthetic test tickers from a loaded scan's lists (Phase 10
    guardrail). No-op in explicit demo mode; logs a tripwire warning if any are
    found in what should be production data."""
    if not scan or os.environ.get("PINPOINT_DEMO_SEED") == "1":
        return scan
    found = set()
    for key in ("focus", "targets", "earnings", "earnings_down", "ipo"):
        df = scan.get(key)
        if df is None or not hasattr(df, "columns") or "ticker" not in getattr(df, "columns", []):
            continue
        hits = df["ticker"].astype(str).isin(SYNTHETIC_TICKERS)
        if hits.any():
            found |= set(df.loc[hits, "ticker"].astype(str))
            scan[key] = df[~hits].reset_index(drop=True)
    if found:
        import logging
        logging.getLogger("pinpoint").warning(
            "quarantined synthetic ticker(s) from live scan: %s "
            "(tripwire — a demo fixture leaked into production data)", ", ".join(sorted(found)))
    return scan
_CSS_PATH = os.path.join(os.path.dirname(__file__), "style.css")


@dataclass
class RegimeView:
    state: str
    rationale: list


# ---------------------------------------------------------------------------
# Bootstrap.
# ---------------------------------------------------------------------------
def page_config(title: str = "Pinpoint") -> None:
    st.set_page_config(page_title=title, layout="wide",
                       initial_sidebar_state="expanded")


def inject_css() -> None:
    with open(_CSS_PATH, encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


def bootstrap(title: str) -> None:
    """Legacy single-page bootstrap (kept for standalone use)."""
    page_config(f"Pinpoint — {title}")
    inject_css()


def refresh_status(scan=None, cloud: bool = None):
    """(label, dot_color) for the 'Last refreshed' header badge. Always an
    ABSOLUTE timestamp — never relative. Local reads the cache meta.json mtime;
    cloud uses the published date + as_of. Dot: green <24h, amber 24-48h, red
    >48h / missing."""
    from datetime import datetime
    GREEN, AMBER, RED = "#00D964", "#E8A317", "#DC2626"
    if cloud is None:
        cloud = cloud_mode()
    if cloud:
        d = (scan or {}).get("date"); aof = (scan or {}).get("as_of")
        try:
            dd = datetime.strptime(d, "%Y-%m-%d").date() if d else None
        except ValueError:
            dd = None
        if dd is None:
            return ("no published data", RED)
        age = (datetime.now().date() - dd).days
        color = GREEN if age <= 0 else AMBER if age == 1 else RED
        return (f"{dd.strftime('%a %-d %b')}, {aof or '—'}", color)
    # local: mtime of the scan cache meta
    from pinpoint import store
    meta = os.path.join(store._cache_dir(), "meta.json")
    if not os.path.exists(meta):
        return ("no scan yet", RED)
    dt = datetime.fromtimestamp(os.path.getmtime(meta))
    age_h = (datetime.now() - dt).total_seconds() / 3600.0
    color = GREEN if age_h < 24 else AMBER if age_h < 48 else RED
    return (dt.strftime("%a %-d %b, %H:%M") + " ET", color)


def next_scheduled_scan():
    """The next weekday 09:30 ET morning-cron time (datetime). Today if it's a
    weekday before 09:30, else the next weekday."""
    from datetime import datetime, timedelta
    now = datetime.now()
    cand = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if not (now < cand and now.weekday() < 5):
        cand += timedelta(days=1)
        while cand.weekday() >= 5:                       # skip Sat/Sun
            cand += timedelta(days=1)
    return cand


def _wl_members() -> set:
    """Watchlist tickers, cached per script run (invalidated on toggle)."""
    m = st.session_state.get("_wl_members")
    if m is None:
        m = set(store.load_watchlist())
        st.session_state["_wl_members"] = m
    return m


def star_button(ticker: str, key: str) -> None:
    """A ★ quick-add toggle. Filled green when on the watchlist, gray when not.
    Single click toggles + persists immediately + toasts (no confirm dialog)."""
    tk = str(ticker).strip().upper()
    if not tk:
        return
    member = tk in _wl_members()
    # st-key-star{on,off}_* drives the CSS color (green vs gray); see style.css.
    kk = f"star{'on' if member else 'off'}_{key}"
    if st.button("★", key=kk, help=("Remove from" if member else "Add to") + " watchlist"):
        if member:
            store.remove_from_watchlist(tk)
            st.toast(f"Removed {tk} from watchlist")
        else:
            store.add_to_watchlist(tk)
            st.toast(f"Added {tk} to watchlist")
        for k in ("_wl_members", "wlpage_key", "wlstrip_key", "wlpage"):
            st.session_state.pop(k, None)
        st.rerun()


def exit_signal_pills_html(keys) -> str:
    """Book exit-signal badge pills (10-EMA break / 20%-above-5EMA climax). Empty
    string when none fired."""
    if not keys:
        return ""
    pills = []
    for k in keys:
        label = flags_mod.EXIT_SIGNAL_LABELS.get(k, k)
        cls = "climax" if k == "climax_trim_5ema" else "exit"
        pills.append(f"<span class='pp-exit {cls}'>{_html.escape(label)}</span>")
    return f"<div class='pp-exits'>{''.join(pills)}</div>"


def sizing_settings() -> dict:
    """User position-sizing settings (account / risk% / heat cap), cached per
    session run. Edited on the Settings page."""
    s = st.session_state.get("_sizing")
    if s is None:
        s = store.load_user_settings()
        st.session_state["_sizing"] = s
    return s


def shares_for(entry, stop) -> tuple[int, float]:
    """(share count, $ risk) for a setup at the user's account/risk%. (0, 0.0)
    when there's no valid plan (missing entry/stop, entry==stop)."""
    s = sizing_settings()
    sh = sizing_mod.compute_shares(s["account"], s["risk_pct"], entry, stop)
    return sh, sizing_mod.compute_dollar_risk(sh, entry, stop)


def heat_badge_html() -> str:
    """Portfolio-heat indicator for the header: 'Heat: 1.8% / 5.0% across 3 open'
    (green/amber/red), or '0% — no open positions'."""
    s = sizing_settings()
    positions = sizing_mod.load_open_positions()
    cap = s["heat_cap_pct"]
    if not positions:
        return ("<span class='pp-heatind' style='color:#6B7280'>"
                "<span class='pp-rdot' style='background:#6B7280'></span>Heat: 0% — no open positions</span>")
    heat = sizing_mod.compute_portfolio_heat(positions, s["account"])
    color = {"green": "#00D964", "amber": "#E8A317", "red": "#DC2626"}[
        sizing_mod.heat_status(heat, cap)]
    return (f"<span class='pp-heatind' style='color:{color}'>"
            f"<span class='pp-rdot' style='background:{color}'></span>"
            f"Heat: {heat:.1f}% / {cap:.1f}% across {len(positions)} open</span>")


def page_header(title: str, subtitle: str = None) -> None:
    """Consistent page header on every page: title (left) + portfolio-heat +
    an absolute 'Last refreshed' timestamp with a status dot (right). `title` may
    contain inline HTML; `subtitle` renders as the usual pp-sub line below."""
    scan = st.session_state.get("scan")
    label, color = refresh_status(scan)
    st.markdown(
        f"<div class='pp-header'><div class='pp-h1'>{title}</div>"
        f"<div class='pp-headmeta'>{heat_badge_html()}"
        f"<span class='pp-refresh'><span class='pp-rdot' style='background:{color}'></span>"
        f"Last refreshed: {_html.escape(label)}</span></div></div>",
        unsafe_allow_html=True)
    if subtitle:
        st.markdown(f"<div class='pp-sub'>{subtitle}</div>", unsafe_allow_html=True)


def sidebar_logo() -> None:
    st.markdown("<div class='pp-logo'>Pin<span class='tick'>point</span></div>",
                unsafe_allow_html=True)


def _n(df) -> int:
    return 0 if df is None else len(df)


def sidebar_footer(scan, cloud: bool) -> None:
    """Sidebar bottom: regime dot + 'Data as of HH:MM' + icon-only refresh."""
    # Show the post-refresh success toast (survives the st.rerun via session).
    msg = st.session_state.pop("_refresh_toast", None)
    if msg:
        st.toast(msg, icon="✅")
    st.markdown("<div class='pp-side-foot'></div>", unsafe_allow_html=True)
    if scan:
        color = _REGIME_COLOR.get(scan["regime"].state, "#6B7280")
        st.markdown(f"<div class='pp-regime'><span class='pp-dot' style='background:{color}'>"
                    f"</span>{scan['regime'].state.upper()}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='pp-asof'>Data as of {_html.escape(scan.get('as_of') or '—')}</div>",
                    unsafe_allow_html=True)
    if st.button("↻", key="side_refresh", help="Refresh scan (live Finviz)"):
        new = load_published() if cloud else full_scan()
        st.session_state["scan"] = new
        if new:
            st.session_state["_refresh_toast"] = (
                f"Refreshed: {_n(new.get('focus'))} Focus, {_n(new.get('targets'))} "
                f"Targets, {_n(new.get('earnings'))} Earnings, {_n(new.get('ipo'))} IPOs")
        for k in ("watchlist_graded_key", "open_cards", "detail_cache", "sector_filter"):
            st.session_state.pop(k, None)
        st.rerun()


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
    lists = {"focus": focus, "targets": uni.df, "earnings": ern.df,
             "earnings_down": ern.down, "ipo": ipo_res.watchlist}
    with st.spinner("Updating snapshot (cache + published blob)..."):
        store.save_scan_cache(lists, reg, as_of, theme_rank=theme_ctx.theme_rank)
        # Watchlist v2: snapshot per name + resolve paper trades (tagged "manual").
        try:
            from pinpoint import watchlist as wl_mod
            from pinpoint import earnings_watch as ew_mod
            _prov = lambda t: ohlcv_mod.fetch_daily(t).df
            wl_mod.snapshot_from_scan(focus, uni.df, ew_mod.active_ctx(),
                                      ohlcv_provider=_prov, source="manual")
            wl_mod.resolve_trades(_prov)       # resolve open paper-trades vs today's OHLCV
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"watchlist snapshot skipped: {exc}")
        # Re-publish latest_scan.json too so the local cache and the cloud blob
        # are updated together (atomically from the user's POV).
        try:
            from datetime import datetime as _dt, timezone as _tz
            now_utc = _dt.now(_tz.utc)
            store.save_published_scan(reg, theme_ctx.theme_rank, lists,
                                      as_of_et=as_of, as_of_utc=now_utc.isoformat(),
                                      index_levels=pipeline.fetch_index_levels())
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"re-publish skipped: {exc}")
    return {"regime": RegimeView(reg.state, reg.rationale), "theme_ctx": theme_ctx,
            "themes": store._themes_list(theme_ctx.theme_rank),
            "focus": focus, "targets": uni.df, "earnings": ern.df,
            "earnings_down": ern.down, "ipo": ipo_res.watchlist,
            "as_of": as_of, "warnings": warnings}


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
            "earnings": _df("earnings"), "earnings_down": _df("earnings_down"),
            "ipo": _df("ipo"), "index_levels": payload.get("index_levels", {}),
            "as_of": payload.get("as_of_et", ""), "date": payload.get("date", ""),
            "as_of_mode": payload.get("as_of_mode", "full"), "warnings": []}


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
  {atr_readout_html(row)}
  {flags_warnings_html(row)}
</div>""", unsafe_allow_html=True)


def earnings_panel(df, title: str, direction: str, universe_tickers=None,
                   key_prefix: str = "") -> None:
    """One earnings-gap panel (up or down) with a ★ quick-add per row. A '◆' marks
    names already in the broad scan universe. Gap-down names are AVOID signals."""
    universe_tickers = universe_tickers or set()
    st.markdown(f"<div class='pp-eh {direction}'>{_html.escape(title)} "
                f"({0 if df is None else len(df)})</div>", unsafe_allow_html=True)
    if df is None or len(df) == 0:
        st.markdown("<div class='pp-empty'>None.</div>", unsafe_allow_html=True)
        return
    color = "#00D964" if direction == "up" else "#FF3366"
    for i, (_, r) in enumerate(df.iterrows()):
        tk = str(r.get("ticker", ""))
        gap = r.get("gap")
        gtxt = (f"{'+' if gap >= 0 else ''}{gap:.1f}%"
                if isinstance(gap, (int, float)) and gap == gap else "—")
        inuni = " ◆" if tk in universe_tickers else ""
        eps = r.get("eps_this_y")
        eps_txt = (f"<span class='eps'>EPS {eps:.0f}%</span>"
                   if isinstance(eps, (int, float)) and eps == eps else "")
        px = f"${r.get('price'):,.2f}" if r.get("price") == r.get("price") else "—"
        rc, sc = st.columns([10, 1], vertical_alignment="center")
        rc.markdown(
            f"<div class='pp-row'><span class='tk'>{_html.escape(tk)}{inuni}</span>"
            f"<span class='px'>{px}</span>"
            f"<span class='score' style='color:{color};width:64px'>{gtxt}</span>"
            f"{rs_chip_html(r.get('rs'))}{eps_txt}</div>", unsafe_allow_html=True)
        with sc:
            star_button(tk, key=f"ern_{key_prefix}{direction}_{i}_{tk}")


def flags_warnings_html(row) -> str:
    """Two-column ✓ Flags (green) / ⚠ Warnings (amber) block (Phase 10 step 5)."""
    def _items(v):
        if v is None:
            return []
        try:
            return [str(x) for x in list(v) if str(x)]
        except TypeError:
            return []
    flags, warns = _items(row.get("flags")), _items(row.get("warnings"))
    if not flags and not warns:
        return ""
    fl = "".join(f"<li>{_html.escape(x)}</li>" for x in flags) or "<li class='none'>—</li>"
    wn = "".join(f"<li>{_html.escape(x)}</li>" for x in warns) or "<li class='none'>—</li>"
    return (f"<div class='pp-fw'>"
            f"<div class='pp-fw-col flags'><div class='h'>✓ Flags</div><ul>{fl}</ul></div>"
            f"<div class='pp-fw-col warns'><div class='h'>⚠ Warnings</div><ul>{wn}</ul></div>"
            f"</div>")


def atr_readout_html(row) -> str:
    """ATR-normalized compression line for the expanded card (Phase 10 step 3)."""
    atr = row.get("atr_14")
    if atr is None or atr != atr:
        return ""
    def _a(k):
        v = row.get(k)
        return f"{abs(v):.2f}" if isinstance(v, (int, float)) and v == v else "—"
    cs = row.get("compression_score")
    cs_txt = f" · coil {cs:.0f}/25" if isinstance(cs, (int, float)) and cs == cs else ""
    return (f"<div class='pp-atr'>ATR(14): ${atr:.2f} &nbsp;|&nbsp; "
            f"5-10: {_a('spread_5_10_atr')} ATR &nbsp;|&nbsp; "
            f"10-20: {_a('spread_10_20_atr')} ATR &nbsp;|&nbsp; "
            f"Price-to-20: {_a('spread_price_20_atr')} ATR{cs_txt}</div>")


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


def tradingview_url(ticker: str) -> str:
    return f"https://www.tradingview.com/chart/?symbol={ticker}"


def rs_chip_html(rs) -> str:
    """Small inline RS chip for the compact row (tier-colored)."""
    import dashboard_logic as dl
    bg, fg = dl.rs_tier(rs if isinstance(rs, (int, float)) else float("nan"))
    val = "—" if (rs is None or (isinstance(rs, float) and rs != rs)) else f"{rs:.0f}"
    return f"<span class='pp-rs' style='background:{bg};color:{fg}'>{val}</span>"


_EF_BADGE = "<span class='pp-ef'>📈 Earnings Flag</span>"


def _row_html(row: dict, spark: str, price: float, chg: Optional[float], ef: bool = False) -> str:
    tk = _html.escape(str(row.get("Ticker", "")))
    pat = _html.escape(str(row.get("Pattern") or "—"))
    sect = _html.escape(str(row.get("Sector") or ""))
    src = str(row.get("Source") or "")
    score = row.get("Score")
    rr = row.get("R:R")
    px = f"${price:,.2f}" if price == price else "—"
    if chg is not None and chg == chg:
        cls = "up" if chg >= 0 else "down"
        arrow = "▲" if chg >= 0 else "▼"
        chg_html = f"<span class='chg {cls}'>{arrow} {abs(chg):.1f}%</span>"
    else:
        chg_html = ""
    score_txt = f"{score:.0f}" if isinstance(score, (int, float)) and score == score else "—"
    rr_txt = f"{rr:.1f}:1" if isinstance(rr, (int, float)) and rr == rr else "—"
    src_cls = "focus" if src == "Focus" else ""
    # score gradient on the 0-100 scale (midpoint 50, strong at 80).
    s_cls = ""
    row_cls = "pp-row"
    if isinstance(score, (int, float)) and score == score:
        s_cls = "e" if score >= 80 else "g" if score >= 65 else "w" if score >= 50 else ""
        if score >= 80:
            row_cls = "pp-row tier1"
    # compact glance trio — entry / exit(stop) / R:R, or "—" when no measured plan
    e, s_, rr_ = row.get("Entry"), row.get("Stop"), row.get("R:R")
    if (isinstance(e, (int, float)) and e == e and isinstance(s_, (int, float)) and s_ == s_):
        rr_txt2 = f"{rr_:.1f}:1" if isinstance(rr_, (int, float)) and rr_ == rr_ else "—"
        sh, _dr = shares_for(e, s_)
        sh_txt = f" · {sh} sh" if sh > 0 else ""
        plan_txt = f"E ${e:,.2f} · X ${s_:,.2f} · {rr_txt2}{sh_txt}"
    else:
        plan_txt = "—"
    plan_html = f"<span class='plan'>{plan_txt}</span>"
    return (f"<div class='{row_cls}' id='pp-card-{tk}'>"
            f"<span class='tk'>{tk}</span>"
            f"<span class='px'>{px} {chg_html}</span>"
            f"<span class='spark'>{spark}</span>"
            f"{rs_chip_html(row.get('RS'))}"
            f"<span class='score {s_cls}'>{score_txt}</span>"
            f"{plan_html}"
            f"<span class='pat'>{pat}</span>"
            f"<span class='sect'>{sect}</span>"
            f"{_EF_BADGE if ef else ''}"
            f"<span class='src {src_cls}'>{_html.escape(src)}</span>"
            f"</div>")


def compact_card(row: dict, detail_fn, key: str, ef: bool = False) -> None:
    """One-row compact stock card (ticker/price/sparkline/RS/score/pattern/R:R/
    sector) with an inline expand to the detail view. Multiple can be open.
    `ef` shows the earnings-flag badge."""
    import dashboard_logic as dl
    tk = str(row.get("Ticker"))
    open_set = st.session_state.setdefault("open_cards", set())
    is_open = tk in open_set

    daily = ohlcv_mod.fetch_daily(tk, cache_only=cloud_mode()).df
    closes = list(daily["Close"]) if daily is not None and len(daily) else []
    spark = dl.sparkline_svg(closes)
    price = float(closes[-1]) if closes else float("nan")
    chg = ((closes[-1] / closes[-2] - 1.0) * 100.0) if len(closes) >= 2 else None

    c1, cstar, c2 = st.columns([22, 1, 1], vertical_alignment="center")
    c1.markdown(_row_html(row, spark, price, chg, ef=ef), unsafe_allow_html=True)
    with cstar:
        star_button(tk, key=f"row_{key}")
    if c2.button("⌃" if is_open else "⌄", key=f"exp_{key}"):
        (open_set.discard if is_open else open_set.add)(tk)
        st.rerun()
    if is_open:
        pr = detail_fn(tk)
        if pr is not None:
            render_detail_inline(pr)


def render_detail_inline(pr) -> None:
    """Expanded card body (Phase 7.7 reading order, internally scrollable):
    1) clean daily chart (weekly via toggle) with right-edge pills,
    2) a plain-English setup paragraph, 3) the 5×2 criterion checklist (support),
    4) an action row (Save · TradingView · Set Alert)."""
    import dashboard_logic as dl
    import charts_plotly as cp

    with st.container(height=660, border=False):
        # 1) chart
        show_weekly = st.toggle("Weekly", key=f"wk_{pr.ticker}", value=False)
        if pr.daily is not None and len(pr.daily):
            if show_weekly:
                wfig = cp.weekly_figure(pr.daily)
                if wfig is not None:
                    st.plotly_chart(wfig, use_container_width=True, key=f"wc_{pr.ticker}")
            fig = cp.daily_figure(pr.daily, entry=pr.entry, stop=pr.stop, target=pr.target,
                                  pattern_bars=pr.pattern_bars, reward_risk=pr.reward_risk)
            if fig is not None:
                st.plotly_chart(fig, use_container_width=True, key=f"dc_{pr.ticker}")
        else:
            st.markdown("<div class='pp-empty'>chart unavailable (OHLCV not cached)</div>",
                        unsafe_allow_html=True)

        # 1b) the trade plan as explicit numbers — always shown, independent of
        # the chart (entry/stop were previously only drawn as chart pills).
        st.markdown(
            "<div class='pp-grid4'>"
            f"<div class='pp-cell'><div class='k'>Entry</div>"
            f"<div class='v green'>${_fmt(pr.entry)}</div></div>"
            f"<div class='pp-cell'><div class='k'>Exit · stop (.89)</div>"
            f"<div class='v red'>${_fmt(pr.stop)}</div></div>"
            f"<div class='pp-cell'><div class='k'>Target</div>"
            f"<div class='v'>${_fmt(pr.target)}</div></div>"
            f"<div class='pp-cell'><div class='k'>R:R</div>"
            f"<div class='v'>{_fmt(pr.reward_risk, 1)}:1</div></div>"
            "</div>", unsafe_allow_html=True)
        _sh, _dr = shares_for(pr.entry, pr.stop)
        if _sh > 0:
            _s = sizing_settings()
            st.markdown(
                f"<div class='pp-sizing'>Shares: <b>{_sh}</b> · Risk: <b>${_dr:,.0f}</b> "
                f"<span class='note'>— based on {_s['risk_pct']:.2f}% account risk; "
                f"adjust in Settings. Research aid, not advice.</span></div>",
                unsafe_allow_html=True)
        # book exit signals (10-EMA close break / 20%-above-5EMA climax)
        _exits = exit_signal_pills_html(getattr(pr, "exit_signals", None))
        if _exits:
            st.markdown(_exits, unsafe_allow_html=True)

        # 2) plain-English explanation
        st.markdown(f"<div class='pp-explain'>{dl.setup_explanation(pr)}</div>",
                    unsafe_allow_html=True)

        # 2b) ATR compression readout + flags/warnings (Phase 10 steps 3/5)
        _cardrow = {"atr_14": pr.atr_14, "compression_score": pr.compression_score,
                    "spread_5_10_atr": pr.spread_5_10_atr,
                    "spread_10_20_atr": pr.spread_10_20_atr,
                    "spread_price_20_atr": pr.spread_price_20_atr,
                    "flags": pr.flags, "warnings": pr.warnings}
        st.markdown(atr_readout_html(_cardrow) + flags_warnings_html(_cardrow),
                    unsafe_allow_html=True)

        # 3) the criteria checklist (supporting role)
        if pr.criteria:
            st.markdown("<div class='pp-section' style='margin:10px 0 4px'>Checklist</div>",
                        unsafe_allow_html=True)
            st.markdown(pills_html(pr.criteria), unsafe_allow_html=True)

        # 4) action row
        a1, a2, a3 = st.columns([1.1, 1.4, 1.2], vertical_alignment="center")
        with a1:
            if st.button("❤ Save", key=f"save_{pr.ticker}"):
                from pinpoint import store
                store.add_to_watchlist(pr.ticker)
                st.toast(f"{pr.ticker} added to watchlist")
        with a2:
            st.markdown(f"<a class='pp-tvlink' href='{tradingview_url(pr.ticker)}' "
                        f"target='_blank'>📊 Open in TradingView ↗</a>", unsafe_allow_html=True)
        with a3:
            if st.button("🔔 Set Alert", key=f"alert_{pr.ticker}"):
                st.toast("Alerts arrive in a later phase", icon="🔔")


def render_treemap(rows, key: str = "treemap"):
    """Sector heatmap as floating rounded colored tiles on the white page (a CSS
    grid — sized by rank weight, colored by RS). Visual-only; the selectbox
    alongside handles filtering. Returns None."""
    import dashboard_logic as dl
    tiles = dl.heatmap_tiles(rows)
    if not tiles:
        st.markdown("<div class='pp-empty'>Theme rankings unavailable.</div>",
                    unsafe_allow_html=True)
        return None
    cells = []
    for t in tiles:
        score = t.get("score")
        rs_txt = f"RS {score:.0f}" if score is not None else ""
        hot = " ◦" if t.get("hot") else ""
        hover = _html.escape(f"{t['label']} · {rs_txt} · top: {t.get('top3','—')}")
        grow = max(0.6, float(t.get("weight", 1.0)))
        cells.append(
            f"<div class='pp-heat-tile' title='{hover}' "
            f"style='flex:{grow} 1 120px;background:{t['color']}'>"
            f"<span class='name'>{_html.escape(str(t['label']))}{hot}</span>"
            f"<span class='rs'>{rs_txt}</span></div>")
    st.markdown(f"<div class='pp-heat'>{''.join(cells)}</div>", unsafe_allow_html=True)
    return None


def _fmt_cap(mc) -> str:
    if not isinstance(mc, (int, float)) or mc != mc or mc <= 0:
        return "—"
    if mc >= 1e12: return f"${mc/1e12:.1f}T"
    if mc >= 1e9:  return f"${mc/1e9:.1f}B"
    if mc >= 1e6:  return f"${mc/1e6:.0f}M"
    return f"${mc:.0f}"


def render_stocks_heatmap(groups) -> None:
    """Stocks-view heatmap: tiles per Targets name, sized by market cap, colored
    by today's % change, grouped under sector headers. Visual-only — the
    selectbox alongside drives the filter (same as the Industries view)."""
    if not groups:
        st.markdown("<div class='pp-empty'>No Targets to map — run a scan.</div>",
                    unsafe_allow_html=True)
        return
    blocks = []
    for g in groups:
        tiles = []
        for t in g["tiles"]:
            chg = t.get("change")
            chg_txt = (f"{'+' if chg >= 0 else ''}{chg:.1f}%"
                       if isinstance(chg, (int, float)) and chg == chg else "—")
            sc = t.get("score")
            sc_txt = f"score {sc:.0f}" if isinstance(sc, (int, float)) and sc == sc else ""
            hover = _html.escape(f"{t['ticker']} · {g['sector']} · {chg_txt} · {sc_txt} · "
                                 f"cap {_fmt_cap(t.get('market_cap'))}")
            grow = max(0.6, float(t.get("weight", 1.0)))
            tiles.append(
                f"<div class='pp-heat-tile' title='{hover}' "
                f"style='flex:{grow} 1 90px;background:{t['color']}'>"
                f"<span class='name'>{_html.escape(t['ticker'])}</span>"
                f"<span class='rs'>{chg_txt}</span></div>")
        blocks.append(
            f"<div class='pp-sheat-head'>{_html.escape(g['sector'])} "
            f"<span class='cap'>{_fmt_cap(g['total_cap'])}</span></div>"
            f"<div class='pp-heat'>{''.join(tiles)}</div>")
    st.markdown("".join(blocks), unsafe_allow_html=True)


def _podium_card_html(rank: int, row: dict) -> str:
    best = rank == 1
    label = "<div class='pp-podium-label'>Best setup today</div>" if best else ""
    tk = _html.escape(str(row.get("ticker")))
    sect = _html.escape(str(row.get("sector") or ""))
    pat = _html.escape(str(row.get("pattern") or "").split(" /")[0] or "—")

    def cell(k, v, cls=""):
        return f"<div class='pp-podium-cell'><div class='k'>{k}</div><div class='v {cls}'>{v}</div></div>"
    e, s, t, rr = row.get("entry"), row.get("stop"), row.get("target"), row.get("reward_risk")
    grid = ("<div class='pp-podium-grid'>"
            + cell("Entry", f"${_fmt(e)}", "green") + cell("Stop", f"${_fmt(s)}", "red")
            + cell("Target", f"${_fmt(t)}") + cell("R:R", f"{_fmt(rr,1)}:1") + "</div>")
    sh, dr = shares_for(e, s)
    size = (f"<div class='pp-podium-size'>Shares: {sh} · Risk: ${dr:,.0f}</div>"
            if sh > 0 else "")
    ef = f"<div class='pp-podium-ef'>{_EF_BADGE}</div>" if row.get("earnings_flag") else ""
    return (f"<div class='pp-podium {'best' if best else ''}'>{label}"
            f"<div class='pp-podium-rank'>#{rank}</div>"
            f"<div class='pp-podium-tk'>{tk} {rs_chip_html(row.get('rs'))}</div>"
            f"<div class='pp-podium-sect'>{sect} · {pat}</div>"
            f"<div class='pp-podium-score'>Score {_fmt(row.get('score'), 1)}</div>"
            f"{grid}{size}{ef}</div>")


def render_podium(focus_rows: list) -> None:
    """Top-3 podium of Focus-source setups. #1 is larger + green-accented; empty
    slots show a 'wait' card. (Caller hides the section entirely if 0 Focus.)"""
    cols = st.columns([1.25, 1, 1], gap="small", vertical_alignment="top")
    for i in range(3):
        with cols[i]:
            if i < len(focus_rows):
                row = focus_rows[i]
                st.markdown(_podium_card_html(i + 1, row), unsafe_allow_html=True)
                pc1, pc2 = st.columns([4, 1], vertical_alignment="center")
                with pc2:
                    star_button(row["ticker"], key=f"pod_{row['ticker']}")
                if pc1.button("Open chart →", key=f"pod_open_{row['ticker']}",
                              use_container_width=True):
                    st.session_state.setdefault("open_cards", set()).add(row["ticker"])
                    st.session_state["scroll_to"] = row["ticker"]
                    st.rerun()
            else:
                st.markdown(f"<div class='pp-podium-empty'>No #{i + 1} setup today — "
                            f"sitting in cash is a position</div>", unsafe_allow_html=True)


def scroll_to_card() -> None:
    """Best-effort smooth-scroll to a just-expanded Top-10 card (after Open chart →)."""
    tk = st.session_state.pop("scroll_to", None)
    if not tk:
        return
    import streamlit.components.v1 as components
    components.html(
        f"<script>setTimeout(function(){{var el=parent.document.getElementById('pp-card-{tk}');"
        f"if(el) el.scrollIntoView({{behavior:'smooth',block:'center'}});}}, 250);</script>",
        height=0)


def tv_block(label: str, tickers: list, key: str) -> None:
    """A 'Copy to TradingView' code block (EXCHANGE:TICKER, copy-icon built in)."""
    import dashboard_logic as dl
    tickers = [t for t in tickers if t]
    if not tickers:
        return
    s = dl.tv_string(tickers)
    st.markdown(f"<div class='pp-tvlabel'>{_html.escape(label)} "
                f"({len(tickers)} tickers) — paste into a TradingView watchlist</div>",
                unsafe_allow_html=True)
    st.code(s, language=None)


def watchlist_strip(graded, max_tiles: int = 5) -> None:
    """Compact at-a-glance strip of up to `max_tiles` watchlist names."""
    if not graded:
        st.markdown("<div class='pp-empty'>No saved names yet — add from any card "
                    "or My Picks.</div>", unsafe_allow_html=True)
        return
    tiles = []
    for pr in graded[:max_tiles]:
        cls = "pass" if pr.classification in ("A+", "near") else "fail"
        mark = "✓" if cls == "pass" else "✗"
        px = f"${pr.price:,.2f}" if pr.price == pr.price else "—"
        tiles.append(f"<span class='pp-wl-tile'><span class='tk'>{_html.escape(pr.ticker)}</span>"
                     f"<span class='pp-num'>{px}</span>{rs_chip_html(pr.rs)}"
                     f"<span class='st {cls}'>{mark}</span></span>")
    more = ("<span class='pp-wl-more'>+ %d more · View all → Watchlist</span>" % (len(graded) - max_tiles)) \
        if len(graded) > max_tiles else ""
    st.markdown(f"<div class='pp-wl-strip'>{''.join(tiles)}{more}</div>", unsafe_allow_html=True)


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
