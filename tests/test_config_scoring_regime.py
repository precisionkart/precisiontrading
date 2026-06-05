"""Tests for config integrity, the scoring engine, and the regime classifier."""

import pandas as pd

from pinpoint.config import (CONFIG, TARGETS_SCREEN, GROWTH_FILTERS, THEME_ETFS,
                             FINVIZ_VIEWS)
from pinpoint.scoring import (score_layers, max_possible_score, LAYER_NAMES,
                              LAYER_LABELS)
from pinpoint import regime as rg


# --- config integrity ------------------------------------------------------
def test_layer_weights_cover_all_layers():
    wd = CONFIG.layers.as_dict()
    assert set(wd) == set(LAYER_NAMES)
    assert all(k in LAYER_LABELS for k in LAYER_NAMES)


def test_max_possible_score_matches_sum():
    assert max_possible_score() == round(sum(CONFIG.layers.as_dict().values()), 3)


def test_targets_screen_has_core_gates():
    for k in ("Price", "Average Volume", "Relative Volume", "52-Week High/Low"):
        assert k in TARGETS_SCREEN
    assert GROWTH_FILTERS["EPS growthqtr over qtr"] == "Over 25%"


def test_theme_etfs_and_views():
    assert "Semiconductors" in THEME_ETFS and "Uranium/Nuclear" in THEME_ETFS
    assert FINVIZ_VIEWS["technical"] == 171


# --- scoring ---------------------------------------------------------------
def test_all_layers_fire_equals_max():
    layers = {n: True for n in LAYER_NAMES}
    res = score_layers(layers)
    assert res.score == max_possible_score()
    assert len(res.fired) == len(LAYER_NAMES)


def test_no_layers_zero():
    assert score_layers({}).score == 0.0


def test_prerequisite_chart_disqualifies():
    res = score_layers({"regime_bull": True, "chart_ok": False})
    assert res.disqualified and res.score == 0.0
    assert "chart" in res.breakdown_str().lower()


def test_breakdown_lists_fired_labels():
    res = score_layers({"valid_pattern": True, "reward_risk": True})
    assert "Valid bullish pattern" in res.breakdown_str()
    assert set(res.fired) == {"valid_pattern", "reward_risk"}


# --- regime ----------------------------------------------------------------
def test_regime_bear_when_below_200():
    reads = rg.from_fundaments({
        "SPY": {"SMA20": "-2%", "SMA50": "-1%", "SMA200": "-3%"},
        "QQQ": {"SMA20": "1%", "SMA50": "2%", "SMA200": "4%"}})
    assert reads.state == "bear"
    assert not reads.is_on


def test_regime_neutral_when_losing_20():
    reads = rg.from_fundaments({
        "SPY": {"SMA20": "-1%", "SMA50": "2%", "SMA200": "8%"},
        "QQQ": {"SMA20": "-0.5%", "SMA50": "3%", "SMA200": "10%"}})
    assert reads.state == "neutral"
    assert reads.is_on            # neutral still allows throttled longs


def test_regime_unknown_defaults_neutral():
    assert rg.from_fundaments({}).state == "neutral"
