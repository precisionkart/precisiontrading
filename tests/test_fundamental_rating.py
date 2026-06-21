"""Tests for the pure fundamental-rating function (Step 2 / 2.5). No I/O."""
import math
import pytest

from pinpoint.fundamental_rating import (
    rate_fundamentals, MARGIN_CAP, INPUT_NAMES, W_EPS_QOQ, W_EPS_THIS_Y,
)

NAN = float("nan")

# real per-quarter margin histories from massive_client (Piece A), newest-first
AAPL_H = [26.6, 29.3, 26.8, 24.9, 26.0, 29.2, 15.5, 25.0]
NVDA_H = [71.5, 63.1, 56.0, 56.5, 42.6, NAN, 55.3, 57.1]
SOFI_H = [128.7, 101.9, 88.9, 64.0, 50.7, 245.4, 46.3, 14.4]


def test_strong_grower_is_A_with_flags():
    r = rate_fundamentals(eps_qoq=120, eps_this_y=80, eps_past5y=40,
                          sales_qoq=60, sales_past5y=40, net_margin=30, roe=40,
                          accumulation=0.8, up_down_volume_ratio=1.8,
                          margin_history=[28, 30, 29, 27, 30])   # stable -> no spike
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


def test_relative_one_time_spike_flags_sofi():
    base = dict(eps_qoq=100, eps_this_y=40, eps_past5y=30, sales_qoq=-8,
                sales_past5y=12, roe=5, accumulation=0.2, up_down_volume_ratio=1.1)
    hot = rate_fundamentals(net_margin=95, margin_history=SOFI_H, **base)
    # latest 128.7 vs median of older quarters (64.0) -> 2.0x >= 1.5 and > 40
    assert hot.flags["one_time_item_warning"] is True
    # SCORING CLAMP still independent: raw 95 margin scores like the cap (50)
    capped = rate_fundamentals(net_margin=MARGIN_CAP, margin_history=SOFI_H, **base)
    assert hot.smr_score == pytest.approx(capped.smr_score)
    assert hot.overall_score == pytest.approx(capped.overall_score)


def test_high_but_stable_margin_does_not_flag_nvda():
    r = rate_fundamentals(eps_qoq=214, eps_this_y=120, eps_past5y=60, sales_qoq=85,
                          sales_past5y=50, net_margin=63, roe=82,
                          accumulation=0.5, up_down_volume_ratio=1.3,
                          margin_history=NVDA_H)
    # 71.5 latest vs ~56 median -> only ~1.27x, below k=1.5 -> NOT one-time
    assert r.flags["one_time_item_warning"] is False
    assert r.flags["strong_margin"] is True
    assert r.missing_inputs == []           # full history available -> no note


def test_short_history_does_not_flag_and_notes_missing():
    # only 1 usable baseline quarter after excluding the latest -> insufficient
    r = rate_fundamentals(net_margin=90, eps_qoq=30, margin_history=[90.0, 80.0])
    assert r.flags["one_time_item_warning"] is False
    assert any("margin_history" in m for m in r.missing_inputs)


def test_all_nan_is_no_data_not_crash():
    r = rate_fundamentals()   # everything default NaN/None, no history
    assert math.isnan(r.overall_score) and r.grade == "N/A"
    assert r.eps_score is None and r.smr_score is None and r.accum_score is None
    # all 9 scoring inputs missing (plus the margin_history insufficiency note)
    assert set(INPUT_NAMES).issubset(set(r.missing_inputs)) and r.n_present == 0
    assert any("margin_history" in m for m in r.missing_inputs)
    assert r.flags["one_time_item_warning"] is False
    assert "no data" in r.summary.lower()


def test_renormalization_not_proportional_drop():
    # eps_qoq -> subscore 100 (weight .5); eps_this_y -> subscore 0 (weight .3)
    both = rate_fundamentals(eps_qoq=60, eps_this_y=0)
    one = rate_fundamentals(eps_qoq=60, eps_this_y=NAN)
    assert both.eps_score == pytest.approx(
        (W_EPS_QOQ * 100 + W_EPS_THIS_Y * 0) / (W_EPS_QOQ + W_EPS_THIS_Y))  # 62.5
    assert one.eps_score == pytest.approx(100.0)
    assert one.eps_score > both.eps_score
    assert "eps_this_y" in one.missing_inputs
