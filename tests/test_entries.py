"""Tests for the .89 liquidity stop, buy-stop, and R:R (entries)."""

import math

from pinpoint.entries import liquidity_stop, compute_setup


def test_liquidity_stop_whole():
    # low just above a whole dollar -> stop at .89 below the whole
    r = liquidity_stop(45.19)
    assert r.cluster_kind == "whole"
    assert r.cluster_level == 45.00
    assert r.stop == 44.89


def test_liquidity_stop_half():
    r = liquidity_stop(45.56)
    assert r.cluster_kind == "half"
    assert r.cluster_level == 45.50
    assert r.stop == 45.49


def test_liquidity_stop_quarter():
    r = liquidity_stop(45.30)
    assert r.cluster_kind == "quarter"
    assert r.cluster_level == 45.25
    assert r.stop == 45.19


def test_liquidity_stop_three_quarter():
    r = liquidity_stop(45.80)
    assert r.cluster_kind == "quarter"
    assert r.cluster_level == 45.75
    assert r.stop == 45.69


def test_liquidity_stop_exact_whole():
    r = liquidity_stop(45.00)
    assert r.cluster_level == 45.00
    assert r.stop == 44.89


def test_compute_setup_rr_pass():
    s = compute_setup(trigger=70.0, support_low=67.0, measured_target=110.0)
    assert s.entry == 70.01
    assert s.stop == 66.89                  # 67.00 whole -> 66.89
    assert s.risk == round(70.01 - 66.89, 2)
    assert s.reward_risk is not None and s.reward_risk >= 5.0
    assert s.rr_ok
    # 3R/5R targets are reported
    assert s.target_3r == round(70.01 + 3 * s.risk, 2)
    assert s.target_5r == round(70.01 + 5 * s.risk, 2)


def test_compute_setup_rr_fail_small_target():
    s = compute_setup(trigger=70.0, support_low=67.0, measured_target=75.0)
    assert s.reward_risk is not None and s.reward_risk < 5.0
    assert not s.rr_ok


def test_compute_setup_no_measured_target_unconfirmed():
    s = compute_setup(trigger=70.0, support_low=67.0, measured_target=None)
    assert s.reward_risk is None
    assert not s.rr_ok
    assert "no measured target" in s.note


def test_compute_setup_invalid_risk():
    # stop ends up above entry -> invalid
    s = compute_setup(trigger=44.90, support_low=45.50, measured_target=60.0)
    assert not s.rr_ok
    assert s.risk <= 0 or math.isnan(s.risk)


def test_compute_setup_rr_exactly_5_passes():
    s = compute_setup(trigger=53.00, support_low=50.00, measured_target=None)
    # build a target that is exactly 5R away from entry
    target = s.entry + 5.0 * s.risk
    s5 = compute_setup(trigger=53.00, support_low=50.00, measured_target=target)
    assert abs(s5.reward_risk - 5.0) < 1e-9
    assert s5.rr_ok                         # >= 5:1 is inclusive


def test_compute_setup_rr_just_below_5_fails():
    s = compute_setup(trigger=53.00, support_low=50.00, measured_target=None)
    target = s.entry + 4.99 * s.risk
    s_low = compute_setup(trigger=53.00, support_low=50.00, measured_target=target)
    assert not s_low.rr_ok


def test_liquidity_stop_invalid_input():
    r = liquidity_stop(float("nan"))
    assert r.cluster_kind == "n/a"
    assert math.isnan(r.stop)
