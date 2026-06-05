"""analyzer.py — "My Picks" grader (spec Section 11 / 7B).

Grades a user-supplied ticker list through the SAME engine the scans use — no
strategy logic is duplicated. Unlike Focus (which only returns qualifiers), the
analyzer grades EVERY name and explains exactly why it does or doesn't qualify:
a per-gate pass/fail checklist plus a plain-English verdict. This is a teaching/
validation tool, so the reasons matter as much as the score.

RS is ranked against a reference universe (the last full-scan snapshot, or a
broad pull) because a percentile is meaningless for a handful of tickers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .config import CONFIG
from . import fundamentals as fundamentals_mod
from . import stage_trend as stage_mod
from . import ohlcv as ohlcv_mod
from . import patterns as patterns_mod
from . import entries as entries_mod
from . import timeframes as timeframes_mod
from . import pipeline
from .rs_rating import compute_rs
from .scoring import score_layers


@dataclass
class GateCheck:
    name: str
    passed: bool
    detail: str


@dataclass
class PickResult:
    ticker: str
    company: Optional[str] = None
    sector: Optional[str] = None
    theme: str = ""
    price: float = float("nan")
    rs: float = float("nan")
    stage: str = ""
    classification: str = "fail"          # "A+" | "near" | "fail"
    gates: list[GateCheck] = field(default_factory=list)
    pattern: Optional[str] = None
    pattern_bars: int = 0
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    reward_risk: Optional[float] = None
    score: float = 0.0
    n_layers: int = 0
    layers: str = ""
    verdict: str = ""
    daily: Optional[pd.DataFrame] = None  # for chart rendering (not serialized)
    note: str = ""

    @property
    def is_pinpoint(self) -> bool:
        return self.classification == "A+"


_SORT_ORDER = {"A+": 0, "near": 1, "fail": 2, "unavailable": 3}


def _verdict(cls: str, gates: list[GateCheck], stage: str, pattern: Optional[str],
            rr: Optional[float]) -> str:
    if cls == "A+":
        return f"Pinpoint setup: all gates pass with R:R {rr:.1f}:1."
    fails = [g for g in gates if not g.passed]
    if cls == "near":
        if pattern is None:
            return f"Gates pass but no valid pattern yet ({stage}) — watchlist it."
        return f"Gates pass but R:R only {rr:.1f}:1 (< 5:1) — wait for a tighter entry."
    # fail
    reasons = "; ".join(g.detail for g in fails[:3])
    return f"Not a Pinpoint setup right now: {reasons}."


def analyze_picks(client, tickers: list[str], regime,
                  reference_universe: Optional[pd.DataFrame] = None,
                  theme_ctx=None, ipo_ctx=None,
                  index_daily: Optional[pd.DataFrame] = None,
                  ohlcv_provider=None,
                  offline_universe: Optional[pd.DataFrame] = None,
                  cache_only: bool = False) -> list[PickResult]:
    """Grade each ticker; return PickResults sorted A+ -> near -> fail.

    `offline_universe` (read-only cloud mode): source each ticker's normalized
    fundament row from this committed snapshot instead of the live Finviz quote
    endpoint — so the cloud app makes ZERO Finviz calls. Tickers absent from the
    snapshot get an "unavailable" result ("Not in latest scan"). `cache_only`
    routes OHLCV through the committed cache only.
    """
    tickers = [t.strip().upper() for t in tickers if t.strip()]
    if not tickers:
        return []
    if ohlcv_provider is None:
        def ohlcv_provider(t):
            return ohlcv_mod.fetch_daily(t, cache_only=cache_only).df
    if index_daily is None:
        index_daily = ohlcv_mod.fetch_daily(CONFIG.regime.benchmarks[0], cache_only=cache_only).df
    index_close = index_daily["Close"] if (index_daily is not None and "Close" in getattr(index_daily, "columns", [])) else None

    unavailable: list[str] = []
    if offline_universe is not None and len(offline_universe):
        by_t = {str(r["ticker"]).upper(): r for _, r in offline_universe.iterrows()}
        rows = {}
        for t in tickers:
            if t in by_t:
                rows[t] = dict(by_t[t])
            else:
                unavailable.append(t)
        reference_universe = reference_universe if reference_universe is not None else offline_universe
    else:
        rows = {t: pipeline.normalize_quote(t, client.fetch_quote_fundament(t)) for t in tickers}
    rows = {t: r for t, r in rows.items() if r.get("price") == r.get("price")}

    # RS against the reference universe + the picks.
    perf_cols = ["perf_week", "perf_month", "perf_quarter", "perf_half", "perf_year"]
    picks_df = pd.DataFrame(list(rows.values()))
    if len(picks_df):
        if reference_universe is not None and len(reference_universe):
            combined = pd.concat([reference_universe[[c for c in perf_cols if c in reference_universe]],
                                  picks_df[[c for c in perf_cols if c in picks_df]]], ignore_index=True)
            rs_all = compute_rs(combined)
            rs_vals = rs_all.iloc[-len(picks_df):].to_numpy()
        else:
            rs_vals = compute_rs(picks_df[[c for c in perf_cols if c in picks_df]]).to_numpy()
        for i, t in enumerate(rows):
            rows[t]["rs"] = rs_vals[i]

    results: list[PickResult] = []
    g = CONFIG.gates
    for t, row in rows.items():
        results.append(_grade_one(t, row, regime, theme_ctx, ipo_ctx,
                                  ohlcv_provider, index_close, g))
    for t in unavailable:
        results.append(PickResult(
            ticker=t, classification="unavailable",
            verdict="Not in latest scan — open this on local for full analysis.",
            note="not in committed snapshot"))
    results.sort(key=lambda r: (_SORT_ORDER.get(r.classification, 3), -r.score))
    return results


def _grade_one(ticker, row, regime, theme_ctx, ipo_ctx, ohlcv_provider, index_close, g) -> PickResult:
    rs = float(row.get("rs", np.nan))
    sector = row.get("sector")
    industry = row.get("industry")
    daily_raw = ohlcv_provider(ticker)
    have_ohlcv = daily_raw is not None and len(daily_raw) >= 30
    d = ohlcv_mod.add_moving_averages(daily_raw) if have_ohlcv else None

    stage = stage_mod.assess_row(row)

    # detect a measured pattern (for the R:R gate) and the best pattern overall.
    pat = patterns_mod.best_pattern(d, require_measured=True) if have_ohlcv else None
    setup = None
    if pat is not None:
        lookback = CONFIG.entry.stop_lookback
        stop_support = float(d["Low"].iloc[-lookback:].min())
        setup = entries_mod.compute_setup(pat.trigger, stop_support, pat.measured_target)

    # ---- gate checklist ----
    price = float(row.get("price", np.nan))
    avgv = float(row.get("avg_volume", np.nan))
    relv = float(row.get("rel_volume", np.nan))
    pbh = float(row.get("pct_below_high", np.nan))
    rr = setup.reward_risk if setup else None

    def chk(name, ok, detail):
        return GateCheck(name, bool(ok), detail)

    gates = [
        chk("Price > $10", price > g.min_price, f"price ${price:.2f}"),
        chk("Avg Vol >= 300k", avgv >= g.min_avg_volume, f"avg vol {avgv:,.0f}"),
        chk("RVOL > 2", relv > g.min_rel_volume, f"RVOL {relv:.2f}"),
        chk("RS > 90", rs >= g.min_rs_rating, f"RS {rs:.0f}" if rs == rs else "RS n/a"),
        chk("Near 52w high", pbh <= g.max_pct_below_high, f"{pbh:.1f}% below high"),
        chk("Stage 2 / MA stack", stage.is_stage2, stage.label),
        chk("Valid pattern", pat is not None,
            pat.label if pat else "no valid measured pattern"),
        chk("R:R >= 5:1", bool(rr is not None and rr >= CONFIG.entry.min_reward_risk),
            f"{rr:.1f}:1" if rr is not None else "n/a"),
    ]

    hard_gate_names = {"Price > $10", "Avg Vol >= 300k", "RVOL > 2", "RS > 90", "Near 52w high"}
    hard_pass = all(c.passed for c in gates if c.name in hard_gate_names)
    has_setup = pat is not None and rr is not None and rr >= CONFIG.entry.min_reward_risk

    if hard_pass and has_setup:
        classification = "A+"
    elif hard_pass:
        classification = "near"
    else:
        classification = "fail"

    # ---- score (computed for every name, qualifier or not) ----
    base_layers = pipeline.snapshot_layers(row, regime, rs, theme_ctx=theme_ctx, ipo_ctx=ipo_ctx)
    if have_ohlcv:
        cont = timeframes_mod.continuity(daily_raw)
        bb = False
        if index_close is not None:
            beta = float(row.get("beta", np.nan))
            bb = stage_mod.beach_ball_residual(d["Close"], index_close, beta=beta).is_beach_ball
        base_layers.update({
            "valid_pattern": pat is not None,
            "tight_contraction": ohlcv_mod.emas_converged(d),
            "correct_ma_reaction": pipeline._riding_mas(d),
            "support_resistance_flip": pipeline._sr_flip(d),
            "timeframe_continuity": cont.aligned,
            "beach_ball": bb,
            "reward_risk": bool(rr is not None and rr >= CONFIG.entry.min_reward_risk),
        })
    result = score_layers(base_layers)

    theme_label = theme_ctx.theme_label(sector, industry) if theme_ctx else ""
    pr = PickResult(
        ticker=ticker, company=row.get("company"), sector=sector, theme=theme_label,
        price=price, rs=rs, stage=stage.label, classification=classification,
        gates=gates, pattern=(pat.label if pat else None),
        pattern_bars=(pat.bars if pat else 0),
        entry=(setup.entry if setup else None), stop=(setup.stop if setup else None),
        target=(setup.measured_target if setup else None),
        reward_risk=(round(rr, 2) if rr is not None else None),
        score=result.score, n_layers=len(result.fired), layers=result.breakdown_str(),
        daily=daily_raw if have_ohlcv else None,
        note="" if have_ohlcv else "no OHLCV available",
    )
    pr.verdict = _verdict(classification, gates, stage.label, pr.pattern, rr)
    return pr
