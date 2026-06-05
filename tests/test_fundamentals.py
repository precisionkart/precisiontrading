"""Tests for growth assessment (fundamentals) and stage/beach-ball (stage_trend)."""

import pandas as pd

from pinpoint.fundamentals import assess_row as growth_row
from pinpoint.stage_trend import classify_from_sma, beach_ball_residual


def test_growth_strong_when_eps_qoq_high():
    prof = growth_row(pd.Series({"eps_qoq": 60.0, "sales_past5y": 10.0}))
    assert prof.strong_growth
    assert prof.meets_eps_qoq


def test_growth_triple_digit_flag():
    prof = growth_row(pd.Series({"sales_past5y": 120.0}))
    assert prof.triple_digit
    assert prof.strong_growth


def test_growth_weak_fails():
    prof = growth_row(pd.Series({"eps_this_y": 5.0, "sales_past5y": 4.0}))
    assert not prof.strong_growth
    assert not prof.triple_digit


def test_growth_acceleration_proxy():
    # current quarter EPS QoQ much higher than the 5yr annual rate
    prof = growth_row(pd.Series({"eps_qoq": 80.0, "eps_past5y": 30.0}))
    assert prof.accelerating_proxy


def test_growth_missing_data_is_safe():
    prof = growth_row(pd.Series({}))
    assert not prof.strong_growth
    assert prof.summary() == "growth n/a"


def test_stage2_from_sma_stack():
    # price above 50 (>0) and 200 (>0), further above 200 than 50 -> 50>200
    read = classify_from_sma(sma20_pct=4.0, sma50_pct=9.0, sma200_pct=22.0)
    assert read.stage == 2
    assert read.is_stage2
    assert read.ma_stack_ok


def test_stage4_below_200():
    read = classify_from_sma(sma20_pct=-3.0, sma50_pct=-1.0, sma200_pct=-5.0)
    assert read.stage == 4
    assert not read.is_stage2


def test_stage3_lost_the_50():
    read = classify_from_sma(sma20_pct=-1.0, sma50_pct=-2.0, sma200_pct=4.0)
    assert read.stage == 3


def test_beach_ball_fires_when_holding_up_in_down_market():
    n = 30
    stock = pd.Series([100.0] * (n + 1))           # flat (held up)
    index = pd.Series([100.0 - i * 0.5 for i in range(n + 1)])  # falling
    bb = beach_ball_residual(stock, index, beta=1.0, window=20)
    assert bb.is_beach_ball
    assert bb.residual > 0


def test_beach_ball_silent_in_up_market():
    n = 30
    stock = pd.Series([100.0 + i for i in range(n + 1)])
    index = pd.Series([100.0 + i for i in range(n + 1)])
    bb = beach_ball_residual(stock, index, beta=1.0, window=20)
    assert not bb.is_beach_ball
