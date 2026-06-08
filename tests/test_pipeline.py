"""End-to-end selftest on fixtures, no network (pipeline + scoring + regime)."""

import pandas as pd

from pinpoint import sample_data
from pinpoint import regime as regime_mod
from pinpoint import pipeline
from pinpoint.scoring import score_layers, max_possible_score


def _build():
    reg = regime_mod.from_fundaments(sample_data.regime_fixture())
    universe = pipeline.normalize_universe(sample_data.universe_df())
    targets = pipeline.build_targets(universe, reg, save_snapshot=False)
    focus = pipeline.build_focus(targets)
    earnings = pipeline.build_earnings(sample_data.earnings_df(), persist=False)
    return reg, targets, focus, earnings


def test_regime_is_bull_on_fixture():
    reg = regime_mod.from_fundaments(sample_data.regime_fixture())
    assert reg.state == "bull"
    assert reg.is_on


def test_all_three_lists_nonempty():
    _, targets, focus, earnings = _build()
    assert len(targets) > 0
    assert len(focus) > 0
    assert len(earnings) > 0


def test_gate_failing_names_excluded_from_targets():
    _, targets, _, _ = _build()
    tickers = set(targets["ticker"].tolist())
    assert tickers.isdisjoint({"LOWV", "FARLO", "CHEAP", "DULLV"})


def test_targets_pass_rs_gate():
    _, targets, _, _ = _build()
    assert (targets["rs"] >= 90).all()


def test_targets_ranked_descending():
    _, targets, _, _ = _build()
    scores = targets["pinpoint_score"].tolist()
    assert scores == sorted(scores, reverse=True)


def test_earnings_gap_up_only():
    _, _, _, earnings = _build()
    tickers = set(earnings["ticker"].tolist())
    assert "BADER" not in tickers          # gapped DOWN
    assert "MEHV" not in tickers           # sub-$10
    assert (earnings["gap"] > 0).all()


def test_scoring_prerequisite_disqualifies():
    layers = {"regime_bull": True, "strong_growth": True,
              "not_earnings_gap_down": False}
    result = score_layers(layers)
    assert result.disqualified
    assert result.score == 0.0


def test_score_within_bounds():
    _, targets, _, _ = _build()
    assert (targets["pinpoint_score"] <= max_possible_score()).all()
    assert (targets["pinpoint_score"] >= 0).all()


def test_normalize_universe_empty():
    assert len(pipeline.normalize_universe(pd.DataFrame())) == 0


def test_evaluate_gates_ignore_rvol():
    row = pd.Series({"price": 50.0, "avg_volume": 1e6, "rel_volume": 0.5,
                     "pct_below_high": 2.0})
    assert not pipeline.evaluate_gates(row).passed          # RVOL 0.5 fails
    assert pipeline.evaluate_gates(row, ignore_rvol=True).passed  # gate dropped


def test_build_earnings_empty_input():
    assert len(pipeline.build_earnings(pd.DataFrame())) == 0


def test_build_targets_uses_precomputed_broad_rs():
    """RS-universe consistency: when an 'rs' column is supplied (the broad-
    reference percentile), build_targets uses it instead of recomputing over the
    gated subset."""
    reg = regime_mod.from_fundaments(sample_data.regime_fixture())
    base = dict(company="C", sector="Technology", industry="Software", price=50.0,
                avg_volume=1_000_000, rel_volume=3.0, beta=1.5, pct_below_high=2.0,
                sma20_pct=3.0, sma50_pct=9.0, sma200_pct=22.0,
                eps_this_y=50.0, eps_past5y=30.0, sales_past5y=30.0, perf_week=1.0)
    # distinct performance so the local percentile makes the top name RS 99
    rows = [dict(base, ticker="AAA", perf_quarter=60.0, perf_half=120.0, perf_year=240.0, perf_month=20.0),
            dict(base, ticker="BBB", perf_quarter=20.0, perf_half=40.0, perf_year=80.0, perf_month=8.0),
            dict(base, ticker="CCC", perf_quarter=5.0, perf_half=10.0, perf_year=20.0, perf_month=2.0)]
    uni = pd.DataFrame(rows)

    # Broad RS says these are all mid-pack (40) -> none clear the RS>90 gate.
    uni_broad = uni.copy()
    uni_broad["rs"] = 40.0
    assert len(pipeline.build_targets(uni_broad, reg, save_snapshot=False)) == 0

    # Without a supplied RS, the local percentile makes the top name 99 -> passes.
    assert len(pipeline.build_targets(uni, reg, save_snapshot=False)) >= 1


def test_build_targets_industry_gate():
    """Industry-RS gate: a name in a non-top-10% industry is dropped (unless
    --no-industry-gate)."""
    from pinpoint.themes import ThemeContext
    reg = regime_mod.from_fundaments(sample_data.regime_fixture())
    base = dict(company="C", sector="Technology", price=50.0, avg_volume=1_000_000,
                rel_volume=3.0, beta=1.5, pct_below_high=2.0, sma20_pct=3.0,
                sma50_pct=9.0, sma200_pct=22.0, eps_this_y=50.0, eps_past5y=30.0,
                sales_past5y=30.0, rs=95.0,
                perf_week=1.0, perf_month=8.0, perf_quarter=20.0, perf_half=40.0, perf_year=80.0)
    uni = pd.DataFrame([dict(base, ticker="HOT", industry="Software"),
                        dict(base, ticker="COLD", industry="Coal")])
    ctx = ThemeContext(theme_rank={}, n_themes=0,
                       industry_rank={"Software": {"pct": 0.99, "rank": 1, "score": 40.0},
                                      "Coal": {"pct": 0.20, "rank": 120, "score": 1.0}})

    gated = pipeline.build_targets(uni, reg, save_snapshot=False, theme_ctx=ctx)
    assert set(gated["ticker"]) == {"HOT"}          # Coal industry gated out
    ungated = pipeline.build_targets(uni, reg, save_snapshot=False, theme_ctx=ctx,
                                     no_industry_gate=True)
    assert set(ungated["ticker"]) == {"HOT", "COLD"}


def test_build_earnings_down_avoid_list():
    """Gap-DOWN earnings list (Phase 10 step 8): gapped down AND closed down."""
    down = pipeline.build_earnings_down(sample_data.earnings_df())
    assert len(down) >= 1
    assert (down["gap"] < 0).all()
    assert (down["change"] < 0).all()
    # gap-up names must NOT appear in the avoid list
    up = set(pipeline.build_earnings(sample_data.earnings_df(), persist=False)["ticker"])
    assert up.isdisjoint(set(down["ticker"]))
