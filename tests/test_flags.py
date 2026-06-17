"""Tests for the plain-English flags/warnings (Phase 10 step 5)."""

import numpy as np
import pandas as pd

from pinpoint import ohlcv as ohlcv_mod
from pinpoint.flags import compute_flags


def _ma(df):
    return ohlcv_mod.add_moving_averages(df)


def test_flags_positive_set():
    # tight, stacked, light-volume name + triple-digit growth + earnings gap
    close = pd.Series(100 + np.sin(np.arange(60) / 8.0) * 0.4)
    vol = np.array([1e6] * 55 + [3e5] * 5)              # dry-up in the last 5 bars
    df = _ma(pd.DataFrame({"Open": close, "High": close + 0.3, "Low": close - 0.3,
                           "Close": close, "Volume": vol}))
    flags, warns = compute_flags(df, compression_score=24.0, slingshot=True,
                                 ef_active=True, eps_this_y=120.0, pct_below_high=1.0)
    assert "🎯 Slingshot" in flags
    assert "Tight Coil" in flags
    assert "Triple-Digit Growth" in flags
    assert "Fresh Earnings Gap" in flags
    assert "Volume Dry-Up" in flags
    assert "Loose" not in warns


def test_warnings_extended_and_loose():
    close = pd.Series(np.linspace(100, 170, 60))        # steep -> fanned + extended
    df = _ma(pd.DataFrame({"Open": close, "High": close + 0.5, "Low": close - 0.5,
                           "Close": close, "Volume": [1e6] * 60}))
    flags, warns = compute_flags(df, compression_score=4.0, pct_below_high=9.0)
    assert "Loose" in warns
    assert "9% from highs" in warns
    assert "Tight Coil" not in flags


def test_exit_signal_ema10_close_break():
    """Close below 10 EMA today after prior close above -> trail-stop exit signal."""
    import numpy as np
    from pinpoint.flags import exit_signals
    # uptrend that holds above the 10 EMA, then a sharp close below it on the last bar
    close = list(np.linspace(100, 130, 40)) + [124.0]   # last bar gaps down under EMA10
    df = _ma(pd.DataFrame({"Open": close, "High": [c + 0.5 for c in close],
                           "Low": [c - 0.5 for c in close], "Close": close,
                           "Volume": [1e6] * len(close)}))
    sigs = exit_signals(df)
    assert "ema10_close_break" in sigs


def test_exit_signal_climax_trim_5ema():
    """Close >= 1.21x the 5 EMA -> 20%-above-5EMA climax trim signal."""
    import numpy as np
    from pinpoint.flags import exit_signals
    base = list(np.linspace(100, 140, 40))
    close = base + [base[-1] * 1.55]                    # parabolic last bar far above 5 EMA
    df = _ma(pd.DataFrame({"Open": close, "High": [c + 0.5 for c in close],
                           "Low": [c - 0.5 for c in close], "Close": close,
                           "Volume": [1e6] * len(close)}))
    sigs = exit_signals(df)
    assert "climax_trim_5ema" in sigs


def test_exit_signals_quiet_on_normal_trend():
    """Smoothly trending data near its EMAs fires neither exit signal."""
    import numpy as np
    from pinpoint.flags import exit_signals
    close = list(100 + np.sin(np.arange(50) / 9.0) * 1.5 + np.arange(50) * 0.1)
    df = _ma(pd.DataFrame({"Open": close, "High": [c + 0.3 for c in close],
                           "Low": [c - 0.3 for c in close], "Close": close,
                           "Volume": [1e6] * len(close)}))
    assert exit_signals(df) == []
    # missing OHLCV -> skip silently
    assert exit_signals(None) == []
    assert exit_signals(pd.DataFrame()) == []
