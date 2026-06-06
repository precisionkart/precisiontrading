"""pipeline.py — build the three ranked watchlists (spec Section 7).

  build_targets  — leaders passing every hard gate (3.3), RS proxy > 90, ranked
                   by pinpoint_score.
  build_earnings — reported yesterday-after-close / today-before-open, price>$10,
                   avg vol>=300k, GAPPED UP ONLY (reaction > numbers), ranked by RS.
  build_focus    — Targets that are also setting up; in Phase 1 this is the top
                   slice of Targets (full OHLCV/pattern/entry enrichment arrives
                   in Phase 3 via ohlcv/patterns/entries/timeframes).

Phase note: several scoring layers (pattern, contraction, beach-ball, time-frame
continuity, R:R, theme rank) require OHLCV/themes and are wired in later phases;
until then they simply do not fire, which keeps scores honest rather than faked.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from .config import (CONFIG, TARGETS_SCREEN, EARNINGS_SCREEN_YESTERDAY,
                     EARNINGS_SCREEN_TODAY, FINVIZ_SORT_QUARTER, GROWTH_FILTERS)
from .finviz_client import COLUMN_CANDIDATES, get_col, to_num, to_pct
from . import fundamentals as fundamentals_mod
from . import stage_trend as stage_mod
from . import ohlcv as ohlcv_mod
from . import patterns as patterns_mod
from . import entries as entries_mod
from . import timeframes as timeframes_mod
from .regime import Regime, BULL
from .rs_rating import compute_rs, PROXY_LABEL
from .scoring import score_layers

logger = logging.getLogger("pinpoint.pipeline")


def save_universe_snapshot(raw_df: pd.DataFrame, label: str = "targets",
                           snapshot_date: date | None = None) -> str | None:
    """Persist the raw merged Finviz frame to data/snapshots/<label>_<date>.json
    BEFORE any filtering (debug paper trail + seeds the Phase 7 My-Picks universe
    snapshot). Returns the path written, or None on failure/empty input."""
    if raw_df is None or len(raw_df) == 0:
        return None
    snap_dir = os.path.join(CONFIG.paths.data_dir, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    d = (snapshot_date or date.today()).isoformat()
    path = os.path.join(snap_dir, f"{label}_{d}.json")
    try:
        raw_df.to_json(path, orient="records", indent=2)
        logger.info("saved universe snapshot: %s (%d rows)", path, len(raw_df))
        return path
    except Exception as exc:  # noqa: BLE001 — never let a snapshot failure break a scan
        logger.warning("could not write snapshot %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Normalisation: merged-Finviz string frame -> tidy numeric frame.
# ---------------------------------------------------------------------------
def normalize_universe(df: pd.DataFrame) -> pd.DataFrame:
    """Defensively map a merged Finviz frame to canonical numeric columns."""
    if df is None or len(df) == 0:
        return pd.DataFrame()

    out = pd.DataFrame(index=df.index)
    out["ticker"] = get_col(df, COLUMN_CANDIDATES["ticker"]).astype("string")
    out["company"] = get_col(df, COLUMN_CANDIDATES["company"]).astype("string")
    out["sector"] = get_col(df, COLUMN_CANDIDATES["sector"]).astype("string")
    out["industry"] = get_col(df, COLUMN_CANDIDATES["industry"]).astype("string")

    # Raw numeric columns (NOT percentages — no fraction scaling).
    out["price"] = get_col(df, COLUMN_CANDIDATES["price"], numeric=True)
    out["avg_volume"] = get_col(df, COLUMN_CANDIDATES["avg_volume"], numeric=True)
    out["rel_volume"] = get_col(df, COLUMN_CANDIDATES["rel_volume"], numeric=True)
    out["beta"] = get_col(df, COLUMN_CANDIDATES["beta"], numeric=True)
    out["atr"] = get_col(df, COLUMN_CANDIDATES["atr"], numeric=True)
    out["rsi"] = get_col(df, COLUMN_CANDIDATES["rsi"], numeric=True)

    # Percentage-semantic columns -> PERCENT units (handles float-fraction vs
    # '%'-string shapes; see finviz_client.to_pct).
    # These arrive as float fractions in finvizfinance 1.3.0 (expect_fraction).
    out["gap"] = get_col(df, COLUMN_CANDIDATES["gap"], pct=True, expect_fraction=True)
    out["sma20_pct"] = get_col(df, COLUMN_CANDIDATES["sma20"], pct=True, expect_fraction=True)
    out["sma50_pct"] = get_col(df, COLUMN_CANDIDATES["sma50"], pct=True, expect_fraction=True)
    out["sma200_pct"] = get_col(df, COLUMN_CANDIDATES["sma200"], pct=True, expect_fraction=True)

    # "52W High" is % distance from the 52-week high (<=0). pct_below_high >= 0.
    high52 = get_col(df, COLUMN_CANDIDATES["high52w"], pct=True, expect_fraction=True)
    out["pct_below_high"] = (-high52).clip(lower=0.0)

    for key in ("perf_week", "perf_month", "perf_quarter", "perf_half", "perf_year"):
        out[key] = get_col(df, COLUMN_CANDIDATES[key], pct=True, expect_fraction=True)

    # EPS/Sales come from the valuation view as legacy '%' strings (not fractions).
    out["eps_this_y"] = get_col(df, COLUMN_CANDIDATES["eps_this_y"], pct=True)
    out["eps_past5y"] = get_col(df, COLUMN_CANDIDATES["eps_past5y"], pct=True)
    out["sales_past5y"] = get_col(df, COLUMN_CANDIDATES["sales_past5y"], pct=True)

    out = out[out["ticker"].notna()].reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# Hard gates (3.3) — every row gets a pass/fail per gate (for the analyzer too).
# ---------------------------------------------------------------------------
@dataclass
class GateResult:
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def evaluate_gates(row: pd.Series, ignore_rvol: bool = False) -> GateResult:
    """Apply the §3.3 universe gates to one normalized row. RS is checked
    separately (it needs the whole universe). `ignore_rvol` drops the relative-
    volume gate for weekend/evening prep runs (RVOL is naturally low off-hours)."""
    g = CONFIG.gates
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    def check(name: str, ok: bool, why: str) -> None:
        checks[name] = bool(ok)
        if not ok:
            reasons.append(why)

    price = row.get("price", np.nan)
    avgv = row.get("avg_volume", np.nan)
    relv = row.get("rel_volume", np.nan)
    pbh = row.get("pct_below_high", np.nan)

    check("price>$10", price > g.min_price, f"price ${price:.2f} <= ${g.min_price:.0f}")
    check("avg_vol>=300k", avgv >= g.min_avg_volume,
          f"avg vol {avgv:,.0f} < {g.min_avg_volume:,}")
    if not ignore_rvol:
        check("rvol>2", relv > g.min_rel_volume, f"RVOL {relv:.2f} <= {g.min_rel_volume}")
    check("near 52w high", pbh <= g.max_pct_below_high,
          f"{pbh:.1f}% below high (> {g.max_pct_below_high:.0f}% limit)")

    return GateResult(passed=all(checks.values()), checks=checks, reasons=reasons)


# ---------------------------------------------------------------------------
# Scoring layers available from the Finviz snapshot (Phase 1 subset).
# ---------------------------------------------------------------------------
def snapshot_layers(row: pd.Series, regime: Regime, rs: float,
                    theme_ctx=None, ipo_ctx=None) -> dict[str, bool]:
    """Derive the layer flags computable from Finviz alone, delegating growth to
    fundamentals.py (3.4) and stage to stage_trend.py (3.5). theme/IPO layers
    fire when their contexts are supplied (Phase 5); OHLCV layers are overlaid by
    enrich_focus."""
    g = CONFIG.gates
    sma20 = row.get("sma20_pct", np.nan)

    growth = fundamentals_mod.assess_row(row)     # Section 3.4
    stage = stage_mod.assess_row(row)             # Section 3.5

    # "riding the 20 EMA" proxy: modestly above the 20-day MA, not extended.
    ma_reaction = bool(0 <= sma20 <= 12) if sma20 == sma20 else False
    # tight contraction proxy: price hugging the 20-day MA (<3% away).
    contraction = bool(0 <= sma20 <= 3) if sma20 == sma20 else False

    sector = row.get("sector")
    industry = row.get("industry")
    hot_theme = bool(theme_ctx and theme_ctx.is_hot_theme(sector, industry))
    top_industry = bool(theme_ctx and theme_ctx.is_top_industry(industry))
    ipo_edge = bool(ipo_ctx and ipo_ctx.get(row.get("ticker")))

    return {
        "regime_bull": regime.state == BULL,
        "tight_contraction": contraction,
        "valid_pattern": False,            # overlaid by enrich_focus (patterns.py)
        "stage_2": stage.is_stage2,
        "support_resistance_flip": False,  # overlaid by enrich_focus
        "correct_ma_reaction": ma_reaction,
        "hot_theme": hot_theme,            # Section 3.10
        "timeframe_continuity": False,     # overlaid by enrich_focus
        "top_industry_group": top_industry,  # Section 3.10
        "strong_growth": growth.strong_growth,
        "ipo_edge": ipo_edge,              # Section 3.11
        "beach_ball": False,               # overlaid by enrich_focus
        "volume_confirmation": bool(row.get("rel_volume", 0) > g.min_rel_volume),
        "reward_risk": False,              # overlaid by enrich_focus (entries.py)
        "earnings_flag": False,            # overlaid by enrich_focus (earnings_watch)
        # prerequisites (default true; set False by later modules):
        "chart_ok": True,
        "not_earnings_gap_down": True,
    }


# ---------------------------------------------------------------------------
# build_targets
# ---------------------------------------------------------------------------
def build_targets(universe: pd.DataFrame, regime: Regime,
                  raw_df: pd.DataFrame | None = None,
                  save_snapshot: bool = True, theme_ctx=None,
                  ipo_ctx=None, ignore_rvol: bool = False) -> pd.DataFrame:
    """Return ranked Targets (3.3-3.5). `universe` is a normalized frame.

    Before any filtering, the RAW merged Finviz frame (`raw_df` if provided,
    else the normalized `universe`) is saved to data/snapshots/ as a debug paper
    trail and to seed the Phase 7 My-Picks universe snapshot.

    RS proxy is computed across the ENTIRE supplied universe (percentile only
    means anything at scale), then the >90 gate is applied alongside the hard
    numeric gates.
    """
    if save_snapshot:
        save_universe_snapshot(raw_df if raw_df is not None else universe, label="targets")

    if universe is None or len(universe) == 0:
        return pd.DataFrame()

    df = universe.copy()
    perf = df[[c for c in ("perf_week", "perf_month", "perf_quarter", "perf_half", "perf_year")
               if c in df.columns]]
    df["rs"] = compute_rs(perf)

    rows = []
    for _, row in df.iterrows():
        gates = evaluate_gates(row, ignore_rvol=ignore_rvol)
        rs = row["rs"]
        rs_ok = (rs == rs) and (rs >= CONFIG.gates.min_rs_rating)
        if not (gates.passed and rs_ok):
            continue
        layers = snapshot_layers(row, regime, rs, theme_ctx=theme_ctx, ipo_ctx=ipo_ctx)
        result = score_layers(layers)
        if result.disqualified:
            continue
        growth = fundamentals_mod.assess_row(row)
        stage = stage_mod.assess_row(row)
        theme_label = theme_ctx.theme_label(row.get("sector"), row.get("industry")) if theme_ctx else ""
        rows.append({
            "ticker": row["ticker"],
            "company": row["company"],
            "sector": row["sector"],
            "industry": row["industry"],
            "theme": theme_label,
            "price": row["price"],
            "rs": rs,
            "rel_volume": row["rel_volume"],
            "beta": row["beta"],
            "pct_below_high": row["pct_below_high"],
            "stage": stage.label,
            "growth": growth.summary(),
            "eps_this_y": row["eps_this_y"],
            "sales_past5y": row["sales_past5y"],
            "pinpoint_score": result.score,
            "layers": result.breakdown_str(),
            "n_layers": len(result.fired),
        })

    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values(["pinpoint_score", "rs"], ascending=False).reset_index(drop=True)
        out.attrs["rs_label"] = PROXY_LABEL
    return out


# ---------------------------------------------------------------------------
# build_earnings  (gap-UP only)
# ---------------------------------------------------------------------------
def build_earnings(raw: pd.DataFrame) -> pd.DataFrame:
    """Earnings-reaction list (3.3 / Section 7): price>$10, avg vol>=300k, and
    GAPPED UP only — any gap-down is excluded regardless of the numbers. Ranked
    by RS proxy."""
    universe = normalize_universe(raw)
    if len(universe) == 0:
        return pd.DataFrame()

    g = CONFIG.gates
    perf = universe[[c for c in ("perf_week", "perf_month", "perf_quarter", "perf_half", "perf_year")
                     if c in universe.columns]]
    universe = universe.copy()
    universe["rs"] = compute_rs(perf) if len(perf.columns) else np.nan

    rows = []
    for _, row in universe.iterrows():
        price = row.get("price", np.nan)
        avgv = row.get("avg_volume", np.nan)
        gap = row.get("gap", np.nan)
        if not (price > g.min_price):
            continue
        if not (avgv >= g.min_avg_volume):
            continue
        if not (gap == gap and gap > 0):     # gap-UP only; NaN/down excluded
            continue
        rows.append({
            "ticker": row["ticker"],
            "company": row["company"],
            "sector": row["sector"],
            "price": price,
            "gap": gap,
            "rel_volume": row.get("rel_volume", np.nan),
            "rs": row.get("rs", np.nan),
            "setup": "earnings flag — watch for light-volume flag then breakout",
        })

    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values("rs", ascending=False, na_position="last").reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# build_focus  (Phase 1 = top slice of Targets; enriched in Phase 3)
# ---------------------------------------------------------------------------
def build_focus(targets: pd.DataFrame, limit: int | None = None) -> pd.DataFrame:
    """The daily shortlist. Phase 1: the highest-scoring Targets. Phase 3 adds
    OHLCV-driven pattern/contraction/entry/stop/R:R enrichment and re-ranks by
    stacked layers."""
    if targets is None or len(targets) == 0:
        return pd.DataFrame()
    limit = limit or CONFIG.output.focus_max
    focus = targets.head(limit).copy()
    focus["entry_trigger"] = np.nan      # use enrich_focus for the real values
    focus["stop"] = np.nan
    focus["reward_risk"] = np.nan
    focus["pattern"] = "(unenriched — call enrich_focus for OHLCV setup)"
    return focus.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Phase 3: OHLCV-enriched Focus. Only names with a valid pattern AND R:R>=5:1
# make Focus; re-ranked by the now-fuller pinpoint_score.
# ---------------------------------------------------------------------------
def _riding_mas(d: pd.DataFrame) -> bool:
    """Correct MA reaction (3.5): close above the 20 EMA and not extended from
    the 10 EMA (riding 5/10/20)."""
    if d is None or len(d) == 0 or "EMA20" not in d.columns:
        return False
    last = d.iloc[-1]
    close = last["Close"]
    if close <= 0 or last["EMA20"] != last["EMA20"]:
        return False
    above20 = close >= last["EMA20"]
    near10 = abs(close - last.get("EMA10", close)) / close <= 0.08
    return bool(above20 and near10)


def _sr_flip(d: pd.DataFrame) -> bool:
    """Support->resistance flip (3.5): price now holds above a level that was
    prior resistance. Conservative: prior 20-60 bar high is exceeded and the
    recent low held near/above it (resistance became support)."""
    if d is None or len(d) < 65:
        return False
    prior_res = float(d["High"].iloc[-60:-20].max())
    last_close = float(d["Close"].iloc[-1])
    recent_low = float(d["Low"].iloc[-10:].min())
    return bool(last_close > prior_res and recent_low >= prior_res * 0.98)


def enrich_focus(targets: pd.DataFrame, universe: pd.DataFrame, regime: Regime,
                 ohlcv_provider=None, index_daily: pd.DataFrame | None = None,
                 limit: int | None = None, theme_ctx=None, ipo_ctx=None) -> pd.DataFrame:
    """Enrich Targets into Focus using OHLCV (3.6-3.8).

    For each target: fetch daily OHLCV, add MAs, detect the best pattern that
    implies a measured target, compute the buy-stop / .89 stop / R:R, time-frame
    continuity and the beach-ball residual, then RE-SCORE. A name only makes
    Focus if it has a valid pattern AND R:R >= 5:1.

    `ohlcv_provider(ticker) -> daily DataFrame` lets the offline selftest inject
    fixtures; it defaults to the live yfinance/Stooq fetch.
    """
    if targets is None or len(targets) == 0:
        return pd.DataFrame()

    if ohlcv_provider is None:
        def ohlcv_provider(t):
            return ohlcv_mod.fetch_daily(t).df

    uni_by_ticker = {r["ticker"]: r for _, r in universe.iterrows()} if universe is not None else {}
    index_close = index_daily["Close"] if (index_daily is not None and "Close" in getattr(index_daily, "columns", [])) else None

    rows = []
    for _, t in targets.iterrows():
        ticker = t["ticker"]
        daily = ohlcv_provider(ticker)
        if daily is None or len(daily) < 30:
            continue
        d = ohlcv_mod.add_moving_averages(daily)

        pat = patterns_mod.best_pattern(d, finviz_signals=None, require_measured=True)
        if pat is None:
            continue
        # Stop hugs the IMMEDIATE pivot (tight coil low over the last few bars),
        # NOT the full pattern low — front-running the breakout for tight risk
        # against the measured move (3.7). A name still mid-base therefore has a
        # wide pivot and naturally fails the R:R gate until it coils at the top.
        lookback = CONFIG.entry.stop_lookback
        stop_support = float(d["Low"].iloc[-lookback:].min())
        setup = entries_mod.compute_setup(pat.trigger, stop_support, pat.measured_target)
        if not setup.rr_ok:
            continue                              # Focus requires R:R >= 5:1

        cont = timeframes_mod.continuity(daily)
        contraction = ohlcv_mod.emas_converged(d)
        # beach-ball needs the index series + beta.
        beta = float(uni_by_ticker.get(ticker, {}).get("beta", np.nan)) if uni_by_ticker else np.nan
        bb_fired = False
        if index_close is not None:
            bb = stage_mod.beach_ball_residual(d["Close"], index_close, beta=beta)
            bb_fired = bb.is_beach_ball

        # recompute layers: snapshot layers (regime/stage/growth/volume) overlaid
        # with the OHLCV-derived layers.
        urow = uni_by_ticker.get(ticker)
        base_layers = (snapshot_layers(urow, regime, t.get("rs", np.nan),
                                       theme_ctx=theme_ctx, ipo_ctx=ipo_ctx)
                       if urow is not None else {"regime_bull": regime.state == BULL,
                                                 "chart_ok": True, "not_earnings_gap_down": True})
        base_layers.update({
            "valid_pattern": True,
            "tight_contraction": bool(contraction),
            "correct_ma_reaction": _riding_mas(d),
            "support_resistance_flip": _sr_flip(d),
            "timeframe_continuity": cont.aligned,
            "beach_ball": bool(bb_fired),
            "reward_risk": True,
        })
        result = score_layers(base_layers)
        if result.disqualified:
            continue

        sector = (urow.get("sector") if urow is not None else t.get("sector"))
        industry = (urow.get("industry") if urow is not None else None)
        theme_label = theme_ctx.theme_label(sector, industry) if theme_ctx else (t.get("theme") or "")
        rows.append({
            "ticker": ticker,
            "company": t.get("company"),
            "sector": t.get("sector"),
            "theme": theme_label,
            "price": t.get("price"),
            "rs": t.get("rs"),
            "stage": t.get("stage"),
            "pattern": pat.label,
            "pattern_bars": pat.bars,
            "finviz_confirmed": pat.finviz_confirmed,
            "entry_trigger": setup.entry,
            "stop": setup.stop,
            "stop_kind": setup.stop_kind,
            "risk": setup.risk,
            "target_5r": setup.target_5r,
            "measured_target": setup.measured_target,
            "reward_risk": round(setup.reward_risk, 2) if setup.reward_risk else None,
            "continuity": cont.score,
            "beach_ball": bb_fired,
            "growth": t.get("growth"),
            "pinpoint_score": result.score,
            "n_layers": len(result.fired),
            "layers": result.breakdown_str(),
        })

    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values(["pinpoint_score", "rs"], ascending=False).reset_index(drop=True)
        if limit:
            out = out.head(limit).reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# LIVE orchestration (Phase 2): fetch from Finviz -> normalize -> build.
# Each returns (DataFrame, warnings). Network failures degrade gracefully.
# ---------------------------------------------------------------------------
@dataclass
class LiveResult:
    df: pd.DataFrame
    warnings: list[str] = field(default_factory=list)
    ok: bool = True


@dataclass
class UniverseResult:
    """Built Targets plus the normalized universe behind them (so Focus can be
    enriched without re-fetching)."""
    df: pd.DataFrame                 # ranked Targets
    universe: pd.DataFrame           # normalized universe (for enrich_focus)
    warnings: list[str] = field(default_factory=list)
    ok: bool = True


def fetch_targets_universe(client, regime: Regime, min_growth: bool = False,
                           theme_ctx=None, ipo_ctx=None,
                           ignore_rvol: bool = False) -> UniverseResult:
    """Fetch the universe once, snapshot it, and build ranked Targets. Returns
    both Targets and the normalized universe for downstream Focus enrichment
    (avoids re-fetching Finviz for --all). `ignore_rvol` drops the relative-
    volume Finviz filter (and gate) for off-hours prep runs."""
    screen = dict(TARGETS_SCREEN)
    if ignore_rvol:
        screen.pop("Relative Volume", None)
    if min_growth:
        screen.update(GROWTH_FILTERS)
    result = client.fetch_universe(screen, order=FINVIZ_SORT_QUARTER, ascend=False)
    if result.empty:
        return UniverseResult(df=pd.DataFrame(), universe=pd.DataFrame(),
                              warnings=list(result.warnings), ok=False)
    universe = normalize_universe(result.df)
    targets = build_targets(universe, regime, raw_df=result.df, theme_ctx=theme_ctx,
                            ipo_ctx=ipo_ctx, ignore_rvol=ignore_rvol)
    return UniverseResult(df=targets, universe=universe,
                          warnings=list(result.warnings), ok=result.ok)


def run_targets(client, regime: Regime, limit: int | None = None,
                min_growth: bool = False) -> LiveResult:
    """Live Targets convenience wrapper (delegates to fetch_targets_universe).

    If `min_growth` is True, the hard EPS/Sales QoQ >= 25% Finviz filters (3.4)
    are added to the screen; otherwise growth is only a weighted scoring layer.
    """
    uni = fetch_targets_universe(client, regime, min_growth=min_growth)
    df = uni.df.head(limit).reset_index(drop=True) if (limit and len(uni.df)) else uni.df
    return LiveResult(df=df, warnings=uni.warnings, ok=uni.ok)


def run_earnings(client, limit: int | None = None) -> LiveResult:
    """Live Earnings: combine the yesterday-after-close and today-before-open
    screens, dedupe on Ticker, then apply the gap-UP-only rule (3.3 / Section 7).
    """
    warnings: list[str] = []
    frames: list[pd.DataFrame] = []
    for label, screen in (("yesterday-after-close", EARNINGS_SCREEN_YESTERDAY),
                          ("today-before-open", EARNINGS_SCREEN_TODAY)):
        res = client.fetch_universe(screen)
        warnings.extend(res.warnings)
        if not res.empty:
            frames.append(res.df)
        else:
            warnings.append(f"earnings screen ({label}) returned no rows.")

    if not frames:
        return LiveResult(df=pd.DataFrame(), warnings=warnings, ok=False)

    combined = pd.concat(frames, ignore_index=True)
    tcol = "Ticker" if "Ticker" in combined.columns else combined.columns[0]
    combined = combined.drop_duplicates(subset=[tcol]).reset_index(drop=True)
    earnings = build_earnings(combined)
    if limit:
        earnings = earnings.head(limit).reset_index(drop=True)
    return LiveResult(df=earnings, warnings=warnings, ok=True)


def normalize_quote(ticker: str, fund: dict) -> dict:
    """Build one normalized universe row from a Finviz quote fundament dict.

    The quote endpoint returns percentages as '%' STRINGS (unlike the screener's
    float fractions), so to_pct handles them without scaling. Crucially it also
    carries current-quarter EPS Q/Q and Sales Q/Q (the most-weighted 3.4 driver),
    which the screener views lack. Reused by analyze_tickers / the My-Picks tool.
    """
    def pct(key):
        return to_pct(fund.get(key))

    # "52W High" looks like "316.94 -3.03%"; the trailing token is the % distance.
    hi = fund.get("52W High") or ""
    hi_pct = to_pct(str(hi).split()[-1]) if hi else float("nan")

    return {
        "ticker": ticker,
        "company": fund.get("Company"),
        "sector": fund.get("Sector"),
        "industry": fund.get("Industry"),
        "price": to_num(fund.get("Price")),
        "avg_volume": to_num(fund.get("Avg Volume")),
        "rel_volume": to_num(fund.get("Rel Volume")),
        "beta": to_num(fund.get("Beta")),
        "atr": to_num(fund.get("ATR")),
        "rsi": to_num(fund.get("RSI")),
        "gap": pct("Gap"),
        "sma20_pct": pct("SMA20"),
        "sma50_pct": pct("SMA50"),
        "sma200_pct": pct("SMA200"),
        "pct_below_high": max(0.0, -hi_pct) if hi_pct == hi_pct else float("nan"),
        "perf_week": pct("Perf Week"),
        "perf_month": pct("Perf Month"),
        "perf_quarter": pct("Perf Quarter"),
        "perf_half": pct("Perf Half Y"),
        "perf_year": pct("Perf Year"),
        "eps_this_y": pct("EPS this Y"),
        "eps_past5y": pct("EPS past 5Y"),
        "sales_past5y": pct("Sales past 5Y"),
        "eps_qoq": pct("EPS Q/Q"),
        "sales_qoq": pct("Sales Q/Q"),
    }


def analyze_tickers(client, tickers: list[str], regime: Regime,
                    reference_universe: pd.DataFrame | None = None,
                    index_daily: pd.DataFrame | None = None,
                    theme_ctx=None, ipo_ctx=None) -> pd.DataFrame:
    """Grade a user-supplied ticker list through the SAME engine (Section 11 /
    7B). Fetches each ticker's fundament, ranks RS against a reference universe
    (Section 7B fidelity note — RS is meaningless for a few names alone), then
    OHLCV-enriches. Returns the enriched Focus-style frame (qualifiers only)."""
    rows = [normalize_quote(t, client.fetch_quote_fundament(t)) for t in tickers]
    mini = pd.DataFrame([r for r in rows if r.get("price") == r.get("price")])
    if len(mini) == 0:
        return pd.DataFrame()

    # RS against the reference universe + the picks (percentile only means
    # something at scale).
    perf_cols = ["perf_week", "perf_month", "perf_quarter", "perf_half", "perf_year"]
    if reference_universe is not None and len(reference_universe):
        combined = pd.concat([reference_universe[[c for c in perf_cols if c in reference_universe]],
                              mini[[c for c in perf_cols if c in mini]]], ignore_index=True)
        rs_all = compute_rs(combined)
        mini = mini.copy()
        mini["rs"] = rs_all.iloc[-len(mini):].to_numpy()
    else:
        mini = mini.copy()
        mini["rs"] = compute_rs(mini[[c for c in perf_cols if c in mini]])

    # build target-like rows for enrich_focus
    targetish = []
    for _, r in mini.iterrows():
        growth = fundamentals_mod.assess_row(r)
        stage = stage_mod.assess_row(r)
        theme_label = theme_ctx.theme_label(r.get("sector"), r.get("industry")) if theme_ctx else ""
        targetish.append({"ticker": r["ticker"], "company": r["company"],
                          "sector": r["sector"], "price": r["price"], "rs": r["rs"],
                          "stage": stage.label, "growth": growth.summary(), "theme": theme_label})
    targets = pd.DataFrame(targetish)

    if index_daily is None:
        index_daily = ohlcv_mod.fetch_daily(CONFIG.regime.benchmarks[0]).df
    return enrich_focus(targets, mini, regime, index_daily=index_daily,
                        theme_ctx=theme_ctx, ipo_ctx=ipo_ctx)


def run_focus(client, regime: Regime, limit: int | None = None,
              min_growth: bool = False) -> LiveResult:
    """Live Focus (Phase 3): live Targets -> OHLCV-enriched Focus. Only names
    with a valid pattern and R:R >= 5:1 survive."""
    uni = fetch_targets_universe(client, regime, min_growth=min_growth)
    if uni.df is None or len(uni.df) == 0:
        return LiveResult(df=pd.DataFrame(), warnings=uni.warnings, ok=False)
    index_daily = ohlcv_mod.fetch_daily(CONFIG.regime.benchmarks[0]).df  # SPY
    focus = enrich_focus(uni.df, uni.universe, regime, index_daily=index_daily, limit=limit)
    return LiveResult(df=focus, warnings=uni.warnings, ok=uni.ok)
