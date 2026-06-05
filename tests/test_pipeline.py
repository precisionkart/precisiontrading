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
    earnings = pipeline.build_earnings(sample_data.earnings_df())
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
