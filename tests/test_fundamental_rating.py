"""Tests for the pure fundamental-rating function (Step 2). No I/O, no fetch."""
import math
import pytest

from pinpoint.fundamental_rating import (
    rate_fundamentals, MARGIN_CAP, INPUT_NAMES, W_EPS_QOQ, W_EPS_THIS_Y,
)

NAN = float("nan")


def test_strong_grower_is_A_with_flags():
    r = rate_fundamentals(eps_qoq=120, eps_this_y=80, eps_past5y=40,
                          sales_qoq=60, sales_past5y=40, net_margin=30, roe=40,
                          accumulation=0.8, up_down_volume_ratio=1.8)
    assert r.grade == "A" and r.overall_score >= 80
    assert r.flags["triple_digit_growth"] is True      # eps_qoq 120
    assert r.flags["monster_growth"] is True
    assert r.flags["strong_margin"] is True
    assert r.flags["one_time_item_warning"] is False
    assert r.missing_inputs == [] and r.n_present == len(INPUT_NAMES)


def test_missing_qoq_strong_annual_still_rates_well():
    r = rate_fundamentals(eps_qoq=NAN, eps_this_y=NAN, eps_past5y=35,
                          sales_qoq=NAN, sales_past5y=30, net_margin=22, roe=25,
                          accumulation=NAN, up_down_volume_ratio=NAN)
    # strong annual numbers must NOT be dragged to zero by the missing QoQ fields
    assert r.overall_score >= 65 and r.grade in ("A", "B")
    assert "eps_qoq" in r.missing_inputs and "sales_qoq" in r.missing_inputs
    assert r.eps_score is not None and r.eps_score >= 90   # past5y=35 -> full mark, renormalized
    assert r.accum_score is None                            # whole component absent
    assert r.overall_score == r.overall_score              # not NaN


def test_one_time_margin_capped_not_rewarded_beyond_cap():
    base = dict(eps_qoq=30, eps_this_y=20, eps_past5y=15, sales_qoq=10,
                sales_past5y=12, roe=8, accumulation=0.2, up_down_volume_ratio=1.1)
    hot = rate_fundamentals(net_margin=95, **base)     # SOFI-like one-time spike
    capped = rate_fundamentals(net_margin=MARGIN_CAP, **base)
    assert hot.flags["one_time_item_warning"] is True
    assert hot.flags["strong_margin"] is True
    # raw 95% margin scores identically to the cap -> not rewarded beyond MARGIN_CAP
    assert hot.overall_score == pytest.approx(capped.overall_score)
    assert hot.smr_score == pytest.approx(capped.smr_score)


def test_all_nan_is_no_data_not_crash():
    r = rate_fundamentals()   # everything default NaN/None
    assert math.isnan(r.overall_score) and r.grade == "N/A"
    assert r.eps_score is None and r.smr_score is None and r.accum_score is None
    assert set(r.missing_inputs) == set(INPUT_NAMES) and r.n_present == 0
    assert "no data" in r.summary.lower()


def test_renormalization_not_proportional_drop():
    # eps_qoq -> subscore 100 (weight .5); eps_this_y -> subscore 0 (weight .3)
    both = rate_fundamentals(eps_qoq=60, eps_this_y=0)
    one = rate_fundamentals(eps_qoq=60, eps_this_y=NAN)
    # with the weak metric present, the component is a weighted blend < 100
    assert both.eps_score == pytest.approx(
        (W_EPS_QOQ * 100 + W_EPS_THIS_Y * 0) / (W_EPS_QOQ + W_EPS_THIS_Y))  # 62.5
    # dropping it RENORMALIZES the surviving metric to full weight -> 100,
    # rather than proportionally keeping the 0 contribution
    assert one.eps_score == pytest.approx(100.0)
    assert one.eps_score > both.eps_score
    assert "eps_this_y" in one.missing_inputs
