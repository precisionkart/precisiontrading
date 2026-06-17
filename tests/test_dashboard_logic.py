"""Tests for the Phase 7.5 dashboard logic: RS tiers, pill mapping, Top-10
builder + sector filter, sector-strength deltas, and the analyzer UI criteria."""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import dashboard_logic as dl  # noqa: E402

from pinpoint import sample_data, analyzer  # noqa: E402
from pinpoint import regime as regime_mod  # noqa: E402


# --- RS tier mapping (Phase 7.6 fluoro) ------------------------------------
def test_rs_tier_thresholds():
    assert dl.rs_tier(95)[0] == "#00D964"              # 90-99 fluoro green
    assert dl.rs_tier(85)[0] == "#FFB800"              # 80-89 amber
    assert dl.rs_tier(75)[0] == "#6B7280"              # 70-79 gray
    assert dl.rs_tier(40)[0] == "#D4D4D8"              # <70 light
    assert dl.rs_tier(float("nan"))[0] == "#D4D4D8"


# --- pill pass/fail color (fluoro) ------------------------------------------
def test_pill_kind():
    assert dl.pill_kind(True) == "pass"
    assert dl.pill_kind(False) == "fail"
    assert dl.PILL_COLORS["pass"]["border"] == "#00D964"
    assert dl.PILL_COLORS["fail"]["border"] == "#FF3366"


# --- sparkline + treemap data ----------------------------------------------
def test_sparkline_svg_up_down():
    up = dl.sparkline_svg([1, 2, 3, 4, 5])
    assert up.startswith("<svg") and "#00D964" in up
    down = dl.sparkline_svg([5, 4, 3, 2, 1])
    assert "#FF3366" in down
    assert dl.sparkline_svg([1]) == ""        # too little data


def test_treemap_data_sizing_and_top3():
    themes = [{"theme": "Semis", "rank": 1, "score": 60.0},
              {"theme": "Energy", "rank": 2, "score": 10.0},
              {"theme": "Healthcare", "rank": 3, "score": -5.0}]
    top10 = pd.DataFrame([{"Ticker": "NVDA", "Sector": "Semis"},
                          {"Ticker": "AMD", "Sector": "Semis"}])
    rows = dl.treemap_data(themes, top10, prior_scores={"Semis": 50.0})
    semis = next(r for r in rows if r["label"] == "Semis")
    assert semis["value"] == 3.0              # rank 1 of 3 -> biggest (n-rank+1)
    assert semis["delta"] == 10.0             # 60 - 50
    assert semis["hot"] is True
    assert "NVDA" in semis["top3"]


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


def test_tv_string_format_and_fallback():
    assert dl.tv_string(["aapl", "agx"]) == "NASDAQ:AAPL,NASDAQ:AGX"
    assert dl.tv_string(["AGX"], exchange_map={"AGX": "NYSE"}) == "NYSE:AGX"
    assert dl.tv_string([" ", None, "X"]) == "NASDAQ:X"


def test_setup_explanation_a_plus():
    from tests.test_analyzer import FakeClient, _strong_fundament, _reference_universe, _ohlcv
    reg = regime_mod.from_fundaments(sample_data.regime_fixture())
    a = analyzer.analyze_picks(FakeClient({"STRONG": _strong_fundament()}), ["STRONG"], reg,
                               reference_universe=_reference_universe(),
                               index_daily=_ohlcv("IDX"), ohlcv_provider=_ohlcv)[0]
    txt = dl.setup_explanation(a)
    assert "triggers on a breakout above" in txt and "reward-to-risk" in txt
    assert "<b>" in txt                      # bold key numbers


def test_setup_explanation_no_pattern():
    from pinpoint.analyzer import PickResult, Criterion
    pr = PickResult(ticker="ZZ", pattern=None, stage="Stage 1 (basing)",
                    criteria=[Criterion("volume", "Volume Confirmation", False, "RVOL 0.5"),
                              Criterion("valid_pattern", "Bullish Pattern", False, "none")])
    txt = dl.setup_explanation(pr)
    assert "no valid pattern" in txt
    assert "falls short on" in txt and "Volume Confirmation" in txt


def test_technical_analysis_prose():
    from tests.test_analyzer import FakeClient, _strong_fundament, _reference_universe, _ohlcv
    reg = regime_mod.from_fundaments(sample_data.regime_fixture())
    pr = analyzer.analyze_picks(FakeClient({"STRONG": _strong_fundament()}), ["STRONG"], reg,
                                reference_universe=_reference_universe(),
                                index_daily=_ohlcv("IDX"), ohlcv_provider=_ohlcv)[0]
    text = dl.technical_analysis(pr)
    assert "STRONG" in text and "R:R" in text


def test_build_podium_excludes_sub_tier2_and_unenriched():
    """Podium = Tier 1/2 (>=65) AND fully enriched only (Phase 10 fix). Sub-65
    scores and rows missing a trade plan must never be labeled 'best setup'."""
    focus = pd.DataFrame([
        # qualifies: score 82, full plan
        dict(ticker="GOOD", sector="Tech", pinpoint_score=82.0, rs=95,
             pattern="Flat base", entry_trigger=89.41, stop=82.89,
             measured_target=110.0, reward_risk=6.3, earnings_flag_active=False),
        # excluded: score 60 (< 65 floor) despite a full plan
        dict(ticker="LOWSC", sector="Tech", pinpoint_score=60.0, rs=88,
             pattern="Base", entry_trigger=50.0, stop=47.0,
             measured_target=70.0, reward_risk=6.0, earnings_flag_active=False),
        # excluded: score 90 but unenriched (no entry/stop/target/rr)
        dict(ticker="NOPLAN", sector="Tech", pinpoint_score=90.0, rs=99,
             pattern="—", entry_trigger=None, stop=None,
             measured_target=None, reward_risk=None, earnings_flag_active=False),
    ])
    podium = dl.build_podium(focus)
    tickers = [r["ticker"] for r in podium]
    assert tickers == ["GOOD"]                       # only the qualifying enriched name
    assert "LOWSC" not in tickers                     # sub-Tier-2 score excluded
    assert "NOPLAN" not in tickers                     # unenriched excluded
    assert podium[0]["entry"] == 89.41 and podium[0]["reward_risk"] == 6.3


def test_build_podium_empty_when_nothing_qualifies():
    focus = pd.DataFrame([
        dict(ticker="MEH", sector="Tech", pinpoint_score=40.0, rs=70,
             pattern="—", entry_trigger=10.0, stop=9.0, measured_target=15.0,
             reward_risk=5.0, earnings_flag_active=False)])
    assert dl.build_podium(focus) == []
    assert dl.build_podium(None) == []
    assert dl.build_podium(pd.DataFrame()) == []


def test_change_color_directions():
    assert dl.change_color(None) == "#6B7280"            # missing -> gray
    assert dl.change_color(0.0) == "#6B7280"             # flat -> gray
    up, down = dl.change_color(4.0), dl.change_color(-4.0)
    assert up.startswith("#") and down.startswith("#") and up != down
    # strong up should be greener than weak up
    assert dl.change_color(5.0) != dl.change_color(0.5)


def test_stocks_heatmap_groups_sizes_and_colors():
    targets = pd.DataFrame([
        dict(ticker="AAA", sector="Technology", market_cap=2e12, change=3.0, pinpoint_score=80),
        dict(ticker="BBB", sector="Technology", market_cap=5e11, change=-2.0, pinpoint_score=70),
        dict(ticker="CCC", sector="Energy", market_cap=8e11, change=1.0, pinpoint_score=60),
    ])
    groups = dl.stocks_heatmap(targets)
    # grouped by sector, sorted by total market cap (Tech 2.5T > Energy 0.8T)
    assert [g["sector"] for g in groups] == ["Technology", "Energy"]
    tech = groups[0]["tiles"]
    assert [t["ticker"] for t in tech] == ["AAA", "BBB"]      # within sector by cap desc
    assert tech[0]["weight"] >= tech[1]["weight"]             # bigger cap -> bigger tile
    assert tech[0]["color"] != tech[1]["color"]               # up vs down colored differently
    # sector filter narrows to one group
    assert [g["sector"] for g in dl.stocks_heatmap(targets, "Energy")] == ["Energy"]
    assert dl.stocks_heatmap(None) == [] and dl.stocks_heatmap(pd.DataFrame()) == []
