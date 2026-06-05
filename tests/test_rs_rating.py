"""Tests for the RS proxy (rs_rating)."""

import numpy as np
import pandas as pd

from pinpoint.rs_rating import weighted_return, rs_rating, compute_rs


def test_rs_rating_range_and_monotonicity():
    scores = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    rs = rs_rating(scores)
    assert rs.min() >= 1 and rs.max() <= 99
    # strongest gets the highest rating
    assert rs.iloc[-1] == rs.max()
    assert rs.iloc[0] == rs.min()


def test_rs_rating_tiny_sample_is_nan():
    # RS is a percentile across a universe; <2 names is meaningless.
    assert rs_rating(pd.Series([1.0])).isna().all()


def test_weighted_return_handles_missing_columns():
    perf = pd.DataFrame({"perf_quarter": [10.0, 20.0], "perf_year": [np.nan, 50.0]})
    wr = weighted_return(perf)
    # row 0 only has the quarter component; row 1 blends both
    assert not np.isnan(wr.iloc[0])
    assert wr.iloc[1] > wr.iloc[0]


def test_compute_rs_stronger_names_rank_higher():
    perf = pd.DataFrame({
        "perf_quarter": [5.0, 50.0, 25.0],
        "perf_year": [10.0, 200.0, 80.0],
    })
    rs = compute_rs(perf)
    assert rs.iloc[1] == rs.max()      # the 50%/200% name is strongest
    assert rs.iloc[0] == rs.min()
