"""Tests for the Phase 7.5 dashboard logic: RS tiers, pill mapping, Top-10
builder + sector filter, sector-strength deltas, and the analyzer UI criteria."""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import dashboard_logic as dl  # noqa: E402

from pinpoint import sample_data, analyzer  # noqa: E402
from pinpoint import regime as regime_mod  # noqa: E402


# --- RS tier mapping -------------------------------------------------------
def test_rs_tier_thresholds():
    assert dl.rs_tier(95) == ("#16A34A", "#FFFFFF")    # 90-99 green
    assert dl.rs_tier(85) == ("#F59E0B", "#FFFFFF")    # 80-89 amber
    assert dl.rs_tier(75) == ("#737373", "#FFFFFF")    # 70-79 gray
    assert dl.rs_tier(40) == ("#D4D4D8", "#0A0A0A")    # <70 light
    assert dl.rs_tier(float("nan")) == ("#D4D4D8", "#0A0A0A")


# --- pill pass/fail color ---------------------------------------------------
def test_pill_kind():
    assert dl.pill_kind(True) == "pass"
    assert dl.pill_kind(False) == "fail"
    assert dl.PILL_COLORS["pass"]["border"] == "#16A34A"
    assert dl.PILL_COLORS["fail"]["border"] == "#DC2626"


# --- Top 10 builder + sector filter ----------------------------------------
def _focus():
    return pd.DataFrame([
        {"ticker": "AAA", "sector": "Technology", "pinpoint_score": 11.0, "rs": 99,
         "pattern": "Flat base / base breakout", "reward_risk": 7.2},
    ])


def _targets():
    return pd.DataFrame([
        {"ticker": "BBB", "sector": "Industrials", "pinpoint_score": 6.0, "rs": 95},
        {"ticker": "CCC", "sector": "Technology", "pinpoint_score": 5.0, "rs": 80},
        {"ticker": "AAA", "sector": "Technology", "pinpoint_score": 11.0, "rs": 99},  # dup of focus
    ])


def test_build_top10_mixed_sources_and_dedup():
    df = dl.build_top10(_focus(), _targets())
    assert list(df.columns) == dl.TOP10_COLUMNS
    # AAA appears once (Focus), not duplicated from targets
    assert (df["Ticker"] == "AAA").sum() == 1
    assert df.iloc[0]["Source"] == "Focus"          # highest score first
    assert set(df["Source"]) == {"Focus", "Watch"}
    assert df.iloc[0]["Rank"] == 1


def test_build_top10_sector_filter():
    df = dl.build_top10(_focus(), _targets(), sector_filter="Technology")
    assert set(df["Sector"]) == {"Technology"}
    assert "BBB" not in set(df["Ticker"])           # Industrials filtered out


def test_build_top10_fills_to_ten_when_short():
    # only 3 unique names -> table has 3 rows (no padding with fakes)
    df = dl.build_top10(_focus(), _targets())
    assert len(df) == 3


# --- sector strength deltas ------------------------------------------------
def test_prior_theme_scores_and_delta():
    history = {"2026-W22": {"Semis": {"rank": 2, "score": 50.0}},
               "2026-W23": {"Semis": {"rank": 1, "score": 63.0}}}
    prior = dl.prior_theme_scores(history)
    assert prior["Semis"] == 50.0
    rows = dl.sector_strength_rows([{"theme": "Semis", "rank": 1, "score": 63.0}], prior)
    assert rows[0]["Δ"] == 13.0
    assert rows[0]["hot"] is True
    assert dl.delta_str(13.0).startswith("▲")
    assert dl.delta_str(-2.0).startswith("▼")
    assert dl.delta_str(None) == "—"


def test_prior_theme_scores_empty_when_single_entry():
    assert dl.prior_theme_scores({"2026-W23": {}}) == {}


# --- analyzer UI criteria (offline) ----------------------------------------
def test_pickresult_has_ui_criteria():
    from tests.test_analyzer import FakeClient, _strong_fundament, _reference_universe, _ohlcv
    reg = regime_mod.from_fundaments(sample_data.regime_fixture())
    res = analyzer.analyze_picks(FakeClient({"STRONG": _strong_fundament()}), ["STRONG"], reg,
                                 reference_universe=_reference_universe(),
                                 index_daily=_ohlcv("IDX"), ohlcv_provider=_ohlcv)
    pr = res[0]
    keys = [c.key for c in pr.criteria]
    assert keys == list(analyzer.UI_CRITERIA_ORDER)
    # the R:R criterion passes for the strong A+ name and carries a value
    rr = next(c for c in pr.criteria if c.key == "reward_risk")
    assert rr.passed and rr.value.endswith(":1")


def test_technical_analysis_prose():
    from tests.test_analyzer import FakeClient, _strong_fundament, _reference_universe, _ohlcv
    reg = regime_mod.from_fundaments(sample_data.regime_fixture())
    pr = analyzer.analyze_picks(FakeClient({"STRONG": _strong_fundament()}), ["STRONG"], reg,
                                reference_universe=_reference_universe(),
                                index_daily=_ohlcv("IDX"), ohlcv_provider=_ohlcv)[0]
    text = dl.technical_analysis(pr)
    assert "STRONG" in text and "R:R" in text
