"""Tests for geometric pattern detection and OHLCV transforms."""

import numpy as np
import pandas as pd

from pinpoint import sample_data
from pinpoint.ohlcv import add_moving_averages, to_weekly, emas_converged
from pinpoint.patterns import (detect_patterns, best_pattern, detect_inside_day,
                               detect_high_tight_flag)
from pinpoint.timeframes import continuity


def _ma(df):
    return add_moving_averages(df)


def test_high_tight_flag_detected_on_fixture():
    df = _ma(sample_data.ohlcv_fixture("NVDA"))
    pat = detect_high_tight_flag(df)
    assert pat is not None
    assert pat.name == "high_tight_flag"
    assert pat.measured_target is not None and pat.measured_target > pat.trigger


def test_best_pattern_requires_measured_target():
    df = _ma(sample_data.ohlcv_fixture("HOOD"))
    pat = best_pattern(df, require_measured=True)
    assert pat is not None
    assert pat.measured_target is not None


def test_inside_day_detection():
    # construct a clear inside day: last bar inside prior bar
    df = pd.DataFrame({
        "Open": [10, 10.5], "High": [11.0, 10.8], "Low": [9.0, 9.5],
        "Close": [10.5, 10.2], "Volume": [1e6, 9e5],
    })
    pat = detect_inside_day(df)
    assert pat is not None
    assert pat.trigger == 10.8
    assert pat.support_low == 9.0          # min(inside low, prior low)


def test_inside_day_absent_when_not_inside():
    df = pd.DataFrame({
        "Open": [10, 10.5], "High": [11.0, 11.5], "Low": [9.0, 9.5],
        "Close": [10.5, 11.2], "Volume": [1e6, 9e5],
    })
    assert detect_inside_day(df) is None


def test_emas_converged_true_when_flat():
    n = 60
    df = pd.DataFrame({
        "Open": [50.0] * n, "High": [50.5] * n, "Low": [49.5] * n,
        "Close": [50.0] * n, "Volume": [1e6] * n,
    })
    df = add_moving_averages(df)
    assert emas_converged(df)              # flat price -> EMAs converge


def test_weekly_resample_shape():
    df = sample_data.ohlcv_fixture("CLS")
    w = to_weekly(df)
    assert len(w) < len(df)
    assert list(w.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_continuity_aligned_up_on_fixture():
    df = sample_data.ohlcv_fixture("CLS")
    c = continuity(df)
    assert 0.0 <= c.score <= 1.0


# --- positive + negative synthetic cases per detector ----------------------
from pinpoint.patterns import (detect_flat_base, detect_flag, detect_falling_wedge,
                               detect_descending_channel, detect_ema_reclaim)


def _flat(n=60, level=100.0, noise=0.5):
    import numpy as np
    t = np.arange(n)
    close = level + noise * np.sin(t / 3.0)
    return pd.DataFrame({"Open": close, "High": close + 0.4, "Low": close - 0.4,
                         "Close": close, "Volume": [1e6] * n})


def test_flat_base_positive_and_negative():
    base = _ma(_flat())                      # tight sideways near highs -> base
    assert detect_flat_base(base) is not None
    import numpy as np
    t = np.arange(80)
    steep = 50 + t * 2.0                      # strong uptrend, no tight base
    trending = _ma(pd.DataFrame({"Open": steep, "High": steep + 1, "Low": steep - 1,
                                 "Close": steep, "Volume": [1e6] * 80}))
    assert detect_flat_base(trending) is None


def test_high_tight_flag_negative_no_surge():
    flat = _ma(_flat(n=80))
    assert detect_high_tight_flag(flat) is None


def test_falling_wedge_requires_uptrend():
    # downtrend overall -> not a (bullish) wedge
    import numpy as np
    t = np.arange(60)
    down = 100 - t * 0.8
    df = _ma(pd.DataFrame({"Open": down, "High": down + 1, "Low": down - 1,
                           "Close": down, "Volume": [1e6] * 60}))
    assert detect_falling_wedge(df) is None
    assert detect_descending_channel(df) is None


def test_ema_reclaim_positive():
    # dip below then close back above the 20 EMA
    import numpy as np
    base = [100.0] * 40 + [92.0, 91.0, 90.0, 95.0, 101.0]
    s = pd.Series(base)
    df = pd.DataFrame({"Open": s, "High": s + 1, "Low": s - 1, "Close": s,
                       "Volume": [1e6] * len(s)})
    df = _ma(df)
    assert detect_ema_reclaim(df) is not None


def test_flag_negative_no_pole():
    flat = _ma(_flat(n=40))
    assert detect_flag(flat) is None
