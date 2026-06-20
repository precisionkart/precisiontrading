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
                     EARNINGS_SCREEN_TODAY, FINVIZ_SORT_QUARTER, GROWTH_FILTERS,
                     RS_REFERENCE_SCREEN)
from .finviz_client import COLUMN_CANDIDATES, get_col, to_num, to_pct
from . import screener as screener_mod
from . import fundamentals as fundamentals_mod
from . import stage_trend as stage_mod
from . import ohlcv as ohlcv_mod
from . import patterns as patterns_mod
from . import entries as entries_mod
from . import timeframes as timeframes_mod
from . import flags as flags_mod
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

    # day's % Change (technical view; float fraction) — for the earnings bull-trap filter.
    out["change"] = get_col(df, COLUMN_CANDIDATES["change"], pct=True, expect_fraction=True)
    # Market cap (overview view; "12.3B" -> 1.23e10 via to_num) — for the Stocks heatmap.
    out["market_cap"] = get_col(df, COLUMN_CANDIDATES["market_cap"], numeric=True)

    # EPS/Sales come from the valuation view as legacy '%' strings (not fractions).
    out["eps_this_y"] = get_col(df, COLUMN_CANDIDATES["eps_this_y"], pct=True)
    out["eps_past5y"] = get_col(df, COLUMN_CANDIDATES["eps_past5y"], pct=True)
    out["sales_past5y"] = get_col(df, COLUMN_CANDIDATES["sales_past5y"], pct=True)

    out = out[out["ticker"].notna()].reset_index(drop=True)
    return out


def _ensure_normalized(df: pd.DataFrame) -> pd.DataFrame:
    """Accept either an already-normalized frame (lowercase 'ticker') or a raw
    Finviz-shaped frame ('Ticker'/'Symbol') and return the normalized form. Lets
    build_earnings* take either shape (selftest fixtures + back-compat tests)."""
    if df is None or len(df) == 0:
        return pd.DataFrame()
    if "ticker" in df.columns:
        return df
    if "Ticker" in df.columns or "Symbol" in df.columns:
        return normalize_universe(df)
    return df


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
    # Top-group RS is now sector-level (Massive has no industry-group screener).
    top_industry = bool(theme_ctx and theme_ctx.is_top_industry(sector))
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
                  ipo_ctx=None, ignore_rvol: bool = False,
                  no_industry_gate: bool = False) -> pd.DataFrame:
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
    # Use a precomputed broad-universe RS if the caller supplied one (the RS-
    # universe consistency fix — Targets RS must be a percentile vs the broad
    # ~580-name universe, not the gated subset). Otherwise compute over `universe`
    # (offline selftest / tests).
    if "rs" not in df.columns or df["rs"].isna().all():
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
        # Industry-RS gate (3.3): industry must be in the top 10% of groups.
        # Only enforced when we actually have industry ranks (live/publish); the
        # cloud path reads already-gated published targets.
        if (theme_ctx is not None and getattr(theme_ctx, "industry_rank", None)
                and not no_industry_gate
                and not theme_ctx.is_top_industry(row.get("sector"))):
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
            "change": row.get("change"),
            "market_cap": row.get("market_cap"),
            "pinpoint_score": result.score,
            "score_legacy": result.score_legacy,
            "tier": result.tier,
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
def _reported_recently(filing_date, within_days: int = 4) -> bool:
    """True if `filing_date` (ISO 'YYYY-MM-DD') is within the last `within_days`
    calendar days — our Massive proxy for 'reported just now' since Polygon has no
    earnings-date screener. A few days' window absorbs filing-vs-report lag."""
    if not filing_date:
        return False
    try:
        fd = date.fromisoformat(str(filing_date)[:10])
    except ValueError:
        return False
    return 0 <= (date.today() - fd).days <= within_days


def build_earnings(universe: pd.DataFrame, persist: bool = True,
                   within_days: int = 4) -> pd.DataFrame:
    """Earnings-reaction list (3.3 / Section 7) off a NORMALIZED universe.

    A name qualifies if it reported within the last few days (latest_filing_date)
    AND its reaction passes the bull-trap fix: gapped up AND closed up AND held
    >= half the opening gap (gap>0, change>0, change>=gap*0.5). Survivors are
    persisted to earnings_watch.json so a flag breakout 1-4 weeks later becomes a
    first-class Focus scoring path. Ranked by RS proxy."""
    from . import earnings_watch as ew

    if universe is None or len(universe) == 0:
        return pd.DataFrame()
    universe = _ensure_normalized(universe)
    if len(universe) == 0:
        return pd.DataFrame()
    universe = universe.copy()
    if "rs" not in universe.columns or universe["rs"].isna().all():
        perf = universe[[c for c in ("perf_week", "perf_month", "perf_quarter", "perf_half", "perf_year")
                         if c in universe.columns]]
        universe["rs"] = compute_rs(perf) if len(perf.columns) else np.nan
    has_filing = "latest_filing_date" in universe.columns

    g = CONFIG.gates
    rows = []
    for _, row in universe.iterrows():
        price = row.get("price", np.nan)
        avgv = row.get("avg_volume", np.nan)
        gap = row.get("gap", np.nan)
        change = row.get("change", np.nan)
        if not (price > g.min_price):
            continue
        if not (avgv >= g.min_avg_volume):
            continue
        if has_filing and not _reported_recently(row.get("latest_filing_date"), within_days):
            continue                                  # only names that just reported
        if not ew.passes_gap_filter(gap, change):     # gap-up + held (no bull trap)
            continue
        rows.append({
            "ticker": row["ticker"],
            "company": row["company"],
            "sector": row["sector"],
            "price": price,
            "gap": gap,
            "change": change,
            "rel_volume": row.get("rel_volume", np.nan),
            "rs": row.get("rs", np.nan),
            "setup": "earnings flag — watch for light-volume flag then breakout",
        })

    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values("rs", ascending=False, na_position="last").reset_index(drop=True)
        if persist:
            ew.housekeeping()                          # expire + prune (gap-up only)
            ew.persist([{"ticker": r["ticker"], "gap": r["gap"], "sector": r["sector"],
                         "theme": ""} for _, r in out.iterrows()])
            # "tracked since" = days the name has been on the watch
            store = {e["ticker"]: e for e in ew.load_store()}
            today = date.today()
            def _since(tk):
                gd = ew._parse(store.get(tk, {}).get("gap_date"))
                return (today - gd).days if gd else 0
            out["tracked_days"] = out["ticker"].map(_since)
    return out


def build_earnings_down(universe: pd.DataFrame, within_days: int = 4) -> pd.DataFrame:
    """Earnings gap-DOWN list (Phase 10 step 8) off a NORMALIZED universe — names
    that just reported AND gapped down AND closed down. These are AVOID signals
    (broken support / distribution), NOT trade candidates. Ranked most-negative
    gap first. Not persisted."""
    if universe is None or len(universe) == 0:
        return pd.DataFrame()
    universe = _ensure_normalized(universe)
    if len(universe) == 0:
        return pd.DataFrame()
    g = CONFIG.gates
    universe = universe.copy()
    if "rs" not in universe.columns or universe["rs"].isna().all():
        perf = universe[[c for c in ("perf_week", "perf_month", "perf_quarter", "perf_half", "perf_year")
                         if c in universe.columns]]
        universe["rs"] = compute_rs(perf) if len(perf.columns) else np.nan
    has_filing = "latest_filing_date" in universe.columns
    rows = []
    for _, row in universe.iterrows():
        price, avgv = row.get("price", np.nan), row.get("avg_volume", np.nan)
        gap, change = row.get("gap", np.nan), row.get("change", np.nan)
        if not (price > g.min_price) or not (avgv >= g.min_avg_volume):
            continue
        if has_filing and not _reported_recently(row.get("latest_filing_date"), within_days):
            continue
        if not (gap == gap and change == change and gap < 0 and change < 0):
            continue                                   # require gap-down AND closed down
        rows.append({"ticker": row["ticker"], "company": row["company"],
                     "sector": row["sector"], "price": price, "gap": gap,
                     "change": change, "rs": row.get("rs", np.nan),
                     "eps_this_y": row.get("eps_this_y", np.nan),
                     "setup": "AVOID — gapped down on earnings (broken support)"})
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values("gap", ascending=True, na_position="last").reset_index(drop=True)
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
                 limit: int | None = None, theme_ctx=None, ipo_ctx=None,
                 earnings_ctx=None) -> pd.DataFrame:
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

    from . import earnings_watch as ew
    if earnings_ctx is None:
        earnings_ctx = ew.active_ctx()

    rows = []
    for _, t in targets.iterrows():
        ticker = t["ticker"]
        daily = ohlcv_provider(ticker)
        if daily is None or len(daily) < 30:
            continue
        d = ohlcv_mod.add_moving_averages(daily)
        lookback = CONFIG.entry.stop_lookback

        # Earnings flag (3.6 ⭐): if the name is on the active earnings watch and
        # is breaking out of its flag today, that is the highest-edge setup — let
        # it into Focus even if no generic measured pattern fires.
        ef = {"detected": False, "ema_zone": None}
        ew_entry = earnings_ctx.get(ticker)
        if ew_entry and ew_entry.get("initial_post_gap_high"):
            ef = patterns_mod.detect_earnings_flag(
                daily, ew_entry.get("gap_date"), ew_entry["initial_post_gap_high"])
        sling = patterns_mod.detect_slingshot(daily)        # step 4

        pat = patterns_mod.best_pattern(d, finviz_signals=None, require_measured=True)
        if pat is not None:
            stop_support = float(d["Low"].iloc[-lookback:].min())
            setup = entries_mod.compute_setup(pat.trigger, stop_support, pat.measured_target)
        elif ef["detected"] or sling["detected"]:
            # synthesize a setup from the flag / slingshot reclaim: breakout
            # trigger + tight pivot + prior-advance projection.
            trigger = float(d["High"].iloc[-min(5, len(d)):].max())
            stop_support = (float(sling["shakeout_low"]) if sling["detected"]
                            and sling.get("shakeout_low") else float(d["Low"].iloc[-lookback:].min()))
            measured = patterns_mod._prior_advance_target(d, trigger, ef.get("flag_days", 10) or 10,
                                                          fallback=trigger * 1.15)
            setup = entries_mod.compute_setup(trigger, stop_support, measured)
        else:
            continue
        # Focus requires R:R >= 5:1 — UNLESS it's a confirmed earnings flag or a
        # slingshot reclaim (both earn inclusion on their own).
        if not setup.rr_ok and not ef["detected"] and not sling["detected"]:
            continue

        cont = timeframes_mod.continuity(daily)
        comp = patterns_mod.atr_compression(d)             # ATR-normalized (step 3)
        contraction = comp["compression_score"] > 0
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
        ef_active = bool(ef["detected"])
        base_layers.update({
            "valid_pattern": True,
            "tight_contraction": bool(contraction),
            "correct_ma_reaction": _riding_mas(d),
            "support_resistance_flip": _sr_flip(d),
            "timeframe_continuity": cont.aligned,
            "beach_ball": bool(bb_fired),
            "reward_risk": bool(setup.rr_ok),
            "earnings_flag": ef_active,
            "slingshot": bool(sling["detected"]),
        })
        result = score_layers(base_layers,
                              partials={"tight_contraction": comp["compression_score"]})
        if result.disqualified:
            continue
        if ef_active:
            ew.mark_triggered(ticker, ef.get("ema_zone"))

        sector = (urow.get("sector") if urow is not None else t.get("sector"))
        industry = (urow.get("industry") if urow is not None else None)
        theme_label = theme_ctx.theme_label(sector, industry) if theme_ctx else (t.get("theme") or "")
        fl, wn = flags_mod.compute_flags(
            d, compression_score=comp["compression_score"], slingshot=sling["detected"],
            ef_active=ef_active, ema_zone=ef.get("ema_zone"),
            eps_this_y=(urow.get("eps_this_y") if urow is not None else None),
            sales_growth=(urow.get("sales_past5y") if urow is not None else None),
            pct_below_high=(urow.get("pct_below_high") if urow is not None else None),
            stage_label=t.get("stage"))
        rows.append({
            "ticker": ticker,
            "company": t.get("company"),
            "sector": t.get("sector"),
            "theme": theme_label,
            "price": t.get("price"),
            "rs": t.get("rs"),
            "stage": t.get("stage"),
            "pattern": pat.label if pat is not None else "Earnings flag breakout",
            "pattern_bars": pat.bars if pat is not None else (ef.get("flag_days") or 0),
            "finviz_confirmed": pat.finviz_confirmed if pat is not None else False,
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
            "earnings_flag_active": ef_active,
            "earnings_flag_ema_zone": ef.get("ema_zone"),
            "gap_date": ew_entry.get("gap_date") if ew_entry else None,
            "gap_pct": ew_entry.get("gap_pct") if ew_entry else None,
            "atr_14": comp["atr_14"],
            "compression_score": comp["compression_score"],
            "spread_5_10_atr": comp["spread_5_10_atr"],
            "spread_10_20_atr": comp["spread_10_20_atr"],
            "spread_price_20_atr": comp["spread_price_20_atr"],
            "slingshot_active": bool(sling["detected"]),
            "slingshot_shakeout_low": sling.get("shakeout_low"),
            "flags": fl,
            "warnings": wn,
            "exit_signals": flags_mod.exit_signals(d),

            "pinpoint_score": result.score,
            "score_legacy": result.score_legacy,
            "tier": result.tier,
            "n_layers": len(result.fired),
            "layers": result.breakdown_str(),
        })

    out = pd.DataFrame(rows)
    if len(out):
        # earnings flags sort to the TOP within their score band (3.6 ⭐).
        out = out.sort_values(["earnings_flag_active", "pinpoint_score", "rs"],
                              ascending=False).reset_index(drop=True)
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
    down: pd.DataFrame | None = None       # earnings gap-DOWN list (step 8)


INDEX_SYMBOLS = ("SPY", "QQQ", "IWM", "DIA")


def fetch_index_levels(symbols=INDEX_SYMBOLS) -> dict:
    """Latest close + overnight change% for the headline index ETFs (Phase 10
    step 9). Uses the OHLCV layer (yfinance/Stooq, cached); a symbol that fails
    to fetch is simply omitted. Returns {sym: {price, change_pct, as_of}}."""
    out: dict[str, dict] = {}
    for sym in symbols:
        try:
            daily = ohlcv_mod.fetch_daily(sym).df
            if daily is None or len(daily) < 2 or "Close" not in daily.columns:
                continue
            last = float(daily["Close"].iloc[-1])
            prev = float(daily["Close"].iloc[-2])
            chg = (last / prev - 1.0) * 100.0 if prev else float("nan")
            as_of = (str(daily.index[-1].date()) if hasattr(daily.index[-1], "date")
                     else str(daily.index[-1]))
            out[sym] = {"price": round(last, 2), "change_pct": round(chg, 2), "as_of": as_of}
        except Exception:  # noqa: BLE001
            continue
    return out


@dataclass
class UniverseResult:
    """Built Targets plus the normalized universe behind them (so Focus can be
    enriched without re-fetching)."""
    df: pd.DataFrame                 # ranked Targets
    universe: pd.DataFrame           # normalized universe (for enrich_focus)
    warnings: list[str] = field(default_factory=list)
    ok: bool = True


def build_universe(client, top_n: int = 500, min_growth: bool = False) -> tuple[pd.DataFrame, list[str]]:
    """Build the normalized leader universe from Massive via the screener:
    all active US stocks -> price/volume filter -> top-N OHLCV enrichment ->
    fundamentals -> §3.3 gates. Returns (normalized_universe, warnings).

    `min_growth` adds the hard EPS/Sales QoQ>=25% filter (3.4) post-enrichment."""
    g = CONFIG.gates
    warnings: list[str] = []
    uni = screener_mod.build_universe_df(client, min_price=g.min_price,
                                         min_avg_volume=g.min_avg_volume)
    if uni is None or len(uni) == 0:
        warnings.append("Massive universe empty (no names passed price/volume).")
        return pd.DataFrame(), warnings
    uni = screener_mod.enrich_with_ohlcv(uni, client, top_n=top_n)
    uni = screener_mod.enrich_with_fundamentals(uni, client)
    uni = screener_mod.apply_universe_gates(
        uni, min_price=g.min_price, min_avg_volume=g.min_avg_volume,
        max_pct_below_high=g.max_pct_below_high, require_above_sma200=True)
    if min_growth and len(uni):
        uni = uni[(uni.get("eps_qoq", pd.Series(dtype=float)) >= CONFIG.fundamentals.min_eps_qoq_growth)
                  & (uni.get("sales_qoq", pd.Series(dtype=float)) >= CONFIG.fundamentals.min_sales_growth)]
        uni = uni.reset_index(drop=True)
    warnings.extend(getattr(client, "notes", []) or [])
    return uni, warnings


def fetch_targets_universe(client, regime: Regime, min_growth: bool = False,
                           theme_ctx=None, ipo_ctx=None,
                           ignore_rvol: bool = False,
                           no_industry_gate: bool = False) -> UniverseResult:
    """Build the universe once from Massive and rank Targets. Returns both
    Targets and the normalized universe for downstream Focus enrichment (avoids
    re-scanning for --all). `ignore_rvol` is accepted for call-site
    compatibility; the screener already filters on average volume, and RVOL is a
    weighted scoring layer, so off-hours prep runs need no special handling."""
    universe, warnings = build_universe(client, min_growth=min_growth)
    if universe is None or len(universe) == 0:
        return UniverseResult(df=pd.DataFrame(), universe=pd.DataFrame(),
                              warnings=warnings, ok=False)

    # RS across the full built universe (percentile only means anything at scale).
    universe = _inject_broad_rs(client, universe, warnings)

    targets = build_targets(universe, regime, raw_df=universe, theme_ctx=theme_ctx,
                            ipo_ctx=ipo_ctx, ignore_rvol=ignore_rvol,
                            no_industry_gate=no_industry_gate)
    return UniverseResult(df=targets, universe=universe, warnings=warnings, ok=True)


_PERF_COLS = ("perf_week", "perf_month", "perf_quarter", "perf_half", "perf_year")


def _inject_broad_rs(client, universe: pd.DataFrame, warnings: list) -> pd.DataFrame:
    """Compute each universe name's RS as a percentile across the full built
    universe and set it on `universe['rs']`. With Massive the screener already
    returns the broad leader set (top-N by volume), so RS is ranked directly
    across it — no separate reference pull needed."""
    try:
        cols = [c for c in _PERF_COLS if c in universe.columns]
        if not cols:
            return universe
        universe = universe.copy()
        universe["rs"] = compute_rs(universe[cols])
        return universe
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"RS computation failed ({exc}); leaving RS unset.")
        return universe


def run_targets(client, regime: Regime, limit: int | None = None,
                min_growth: bool = False) -> LiveResult:
    """Live Targets convenience wrapper (delegates to fetch_targets_universe).

    If `min_growth` is True, the hard EPS/Sales QoQ >= 25% Finviz filters (3.4)
    are added to the screen; otherwise growth is only a weighted scoring layer.
    """
    uni = fetch_targets_universe(client, regime, min_growth=min_growth)
    df = uni.df.head(limit).reset_index(drop=True) if (limit and len(uni.df)) else uni.df
    return LiveResult(df=df, warnings=uni.warnings, ok=uni.ok)


def run_earnings(client, limit: int | None = None,
                 universe: pd.DataFrame | None = None) -> LiveResult:
    """Live Earnings off Massive: from the normalized leader universe, keep names
    that reported in the last few days (latest_filing_date) and apply the
    gap-UP-only rule (3.3 / Section 7). Polygon has no earnings-date screener, so
    this surfaces post-earnings reactions among the scanned leaders rather than
    the entire market's reporters.

    Pass `universe` to reuse an already-built scan (avoids a second full pass);
    otherwise the universe is built here."""
    warnings: list[str] = []
    if universe is None or len(universe) == 0:
        universe, warnings = build_universe(client)
    if universe is None or len(universe) == 0:
        return LiveResult(df=pd.DataFrame(), warnings=warnings, ok=False)

    earnings = build_earnings(universe)
    down = build_earnings_down(universe)               # gap-DOWN avoid list (step 8)
    if limit:
        earnings = earnings.head(limit).reset_index(drop=True)
        down = down.head(limit).reset_index(drop=True)
    return LiveResult(df=earnings, warnings=warnings, ok=True, down=down)


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
