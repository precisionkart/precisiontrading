"""Tests for ohlcv transforms + cache path and time-frame continuity."""

import pandas as pd

from pinpoint import sample_data
from pinpoint import ohlcv as o
from pinpoint.timeframes import continuity, _trend_up


def test_add_moving_averages_columns():
    df = o.add_moving_averages(sample_data.ohlcv_fixture("AAA"))
    for c in ("EMA5", "EMA10", "EMA20", "SMA50", "SMA200"):
        assert c in df.columns
        assert df[c].notna().any()


def test_to_weekly_aggregates():
    daily = sample_data.ohlcv_fixture("BBB")
    w = o.to_weekly(daily)
    assert 0 < len(w) < len(daily)
    # weekly high >= weekly close everywhere
    assert (w["High"] >= w["Close"]).all()


def test_emas_converged_true_and_false():
    flat = pd.DataFrame({"Open": [50] * 60, "High": [50.2] * 60, "Low": [49.8] * 60,
                         "Close": [50.0] * 60, "Volume": [1e6] * 60})
    assert o.emas_converged(o.add_moving_averages(flat))
    trending = sample_data.ohlcv_fixture("CCC")   # strong surge -> not converged
    assert not o.emas_converged(o.add_moving_averages(trending))


def test_looks_ohlcv():
    assert o._looks_ohlcv(sample_data.ohlcv_fixture("DDD"))
    assert not o._looks_ohlcv(pd.DataFrame())
    assert not o._looks_ohlcv(pd.DataFrame({"Close": [1, 2]}))


def test_fetch_daily_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(o, "_cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr(o, "MASSIVE_API_KEY", "")     # exercise the yfinance path
    calls = {"n": 0}

    def fake_yf(ticker, period="2y"):
        calls["n"] += 1
        return sample_data.ohlcv_fixture(ticker)

    monkeypatch.setattr(o, "_fetch_yfinance", fake_yf)
    r1 = o.fetch_daily("XYZ")
    assert r1.source == "yfinance" and not r1.empty
    r2 = o.fetch_daily("XYZ")               # second call should hit cache
    assert r2.source == "cache"
    assert calls["n"] == 1


def test_fetch_daily_falls_back_to_stooq(tmp_path, monkeypatch):
    monkeypatch.setattr(o, "_cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr(o, "MASSIVE_API_KEY", "")
    monkeypatch.setattr(o, "_fetch_yfinance", lambda t, period="2y": pd.DataFrame())
    monkeypatch.setattr(o, "_fetch_stooq", lambda t: sample_data.ohlcv_fixture(t))
    r = o.fetch_daily("ABC", use_cache=False)
    assert r.source == "stooq" and not r.empty


def test_fetch_daily_all_fail_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(o, "_cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr(o, "MASSIVE_API_KEY", "")
    monkeypatch.setattr(o, "_fetch_yfinance", lambda t, period="2y": pd.DataFrame())
    monkeypatch.setattr(o, "_fetch_stooq", lambda t: pd.DataFrame())
    r = o.fetch_daily("NOPE", use_cache=False)
    assert r.empty and not r.ok


def test_continuity_score_bounds_and_insufficient():
    c = continuity(sample_data.ohlcv_fixture("EEE"))
    assert 0.0 <= c.score <= 1.0
    short = continuity(sample_data.ohlcv_fixture("FFF").iloc[:10])
    assert short.score == 0.0 and not short.aligned
