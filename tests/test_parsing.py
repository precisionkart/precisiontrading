"""Tests for to_num() and defensive column access (finviz_client)."""

import math

import numpy as np
import pandas as pd

from pinpoint.finviz_client import to_num, to_pct, get_col, pick_column


def test_to_num_basic():
    assert to_num("12.34%") == 12.34
    assert to_num("$45.56") == 45.56
    assert to_num("1.5M") == 1_500_000
    assert to_num("2.4B") == 2_400_000_000
    assert to_num("300K") == 300_000
    assert to_num("1.2T") == 1.2e12


def test_to_num_missing_values():
    for v in ("-", "", "—", "N/A", None, "nan"):
        assert math.isnan(to_num(v))


def test_to_num_unicode_dashes_are_nan():
    # hyphen, figure dash, en dash, em dash, horizontal bar, NBSP-only
    for v in ("‐", "‒", "–", "—", "―", " ", " - "):
        assert math.isnan(to_num(v)), repr(v)


def test_to_num_unicode_minus_is_negative():
    assert to_num("−3.5%") == -3.5      # U+2212 MINUS SIGN
    assert to_num("−1.2M") == -1_200_000


def test_to_num_internal_spaces_and_nbsp():
    assert to_num("1 234") == 1234
    assert to_num("12.5 %") == 12.5


def test_to_num_negatives_and_parens():
    assert to_num("-3.5%") == -3.5
    assert to_num("(2.0)") == -2.0
    assert to_num("-1.5M") == -1_500_000


def test_to_num_passthrough_numeric():
    assert to_num(5) == 5.0
    assert to_num(2.5) == 2.5


def test_to_pct_string_percent_stays_percent():
    # legacy '%'-string columns (valuation EPS This Y, etc.)
    assert to_pct("15.09%") == 15.09
    assert to_pct("-2.10%") == -2.10
    assert to_pct("256.47%") == 256.47


def test_to_pct_float_fraction_scaled_to_percent():
    # finvizfinance 1.3.0 returns SMA/52W/Gap/Perf as float fractions
    assert abs(to_pct(0.0708) - 7.08) < 1e-9
    assert abs(to_pct(-0.1829) - (-18.29)) < 1e-9
    assert abs(to_pct(0.0466) - 4.66) < 1e-9


def test_to_pct_missing_is_nan():
    assert math.isnan(to_pct("-"))
    assert math.isnan(to_pct(None))


def test_get_col_pct_mixed_shapes():
    df = pd.DataFrame({"SMA20": [0.0708, -0.0157], "52W High": ["-2.10%", "-18.29%"]})
    sma = get_col(df, ["SMA20"], pct=True)
    assert abs(sma.iloc[0] - 7.08) < 1e-9
    hi = get_col(df, ["52W High", "52W High"], pct=True)
    assert abs(hi.iloc[1] - (-18.29)) < 1e-9


def test_pick_column_case_insensitive():
    df = pd.DataFrame({"Rel Volume": [1], "Ticker": ["X"]})
    assert pick_column(df, ["Relative Volume", "Rel Volume"]) == "Rel Volume"
    assert pick_column(df, ["Symbol", "Ticker"]) == "Ticker"
    assert pick_column(df, ["Nonexistent"]) is None


def test_get_col_missing_returns_nan_series():
    df = pd.DataFrame({"Ticker": ["A", "B"]})
    s = get_col(df, ["Beta"], numeric=True)
    assert len(s) == 2
    assert s.isna().all()


def test_get_col_numeric_parses_strings():
    df = pd.DataFrame({"Price": ["10.50", "-", "1.2M"]})
    s = get_col(df, ["Price"], numeric=True)
    assert s.iloc[0] == 10.5
    assert np.isnan(s.iloc[1])
    assert s.iloc[2] == 1_200_000
