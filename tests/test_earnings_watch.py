"""Tests for earnings_watch persistence + the bull-trap gap filter (Phase 9)."""

from datetime import date, timedelta

import pandas as pd
import pytest

from pinpoint import earnings_watch as ew
from pinpoint import sample_data


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(ew, "_store_path", lambda: str(tmp_path / "earnings_watch.json"))


def _ohlcv(_t):
    return sample_data.ohlcv_fixture("X")


# --- bull-trap filter ------------------------------------------------------
def test_gap_filter_cases():
    assert ew.passes_gap_filter(8.0, 6.0)          # gap up, held -> include
    assert not ew.passes_gap_filter(-5.0, -3.0)    # gapped down -> exclude
    assert not ew.passes_gap_filter(8.0, -2.0)     # gap up, closed negative (SOFI bull trap)
    assert not ew.passes_gap_filter(8.0, 3.0)      # gap up but gave back >50% (3 < 4)
    assert ew.passes_gap_filter(8.0, 4.0)          # held exactly half -> include


# --- persist + load_active window ------------------------------------------
def test_persist_and_active_window():
    d0 = date(2026, 5, 1)
    n = ew.persist([{"ticker": "ABC", "gap": 9.0, "sector": "Technology", "theme": "Technology #1"}],
                   ohlcv_provider=_ohlcv, today=d0)
    assert n == 1
    store = ew.load_store()
    assert store[0]["ticker"] == "ABC" and store[0]["status"] == "active"
    assert store[0]["initial_post_gap_high"] is not None

    assert ew.load_active(today=d0 + timedelta(days=3)) == []     # too soon (<7d)
    active = ew.load_active(today=d0 + timedelta(days=14))        # in window
    assert [e["ticker"] for e in active] == ["ABC"]
    assert ew.load_active(today=d0 + timedelta(days=40)) == []    # past 4 weeks


def test_dedup_within_few_days():
    d0 = date(2026, 5, 1)
    ew.persist([{"ticker": "ABC", "gap": 9.0}], ohlcv_provider=_ohlcv, today=d0)
    ew.persist([{"ticker": "ABC", "gap": 9.0}], ohlcv_provider=_ohlcv, today=d0 + timedelta(days=2))
    assert len(ew.load_store()) == 1                              # not re-added


def test_mark_expired_and_prune():
    d0 = date(2026, 1, 1)
    ew.persist([{"ticker": "OLD", "gap": 9.0}], ohlcv_provider=_ohlcv, today=d0)
    ew.mark_expired(today=d0 + timedelta(days=40))
    assert ew.load_store()[0]["status"] == "expired"
    ew.prune_old(today=d0 + timedelta(days=120))
    assert ew.load_store() == []                                 # dropped > 90 days


def _gap_flag_ohlcv(fill_gap=False):
    """Synthetic: volatile pre-gap base, an earnings gap to ~60, a 15-bar
    light-volume flag holding the gap (or filling it), then a breakout."""
    import numpy as np
    rows = []
    # 40 pre-gap bars ~50, wide range (ATR ~2.5) so EMA touch windows are generous
    for i in range(40):
        c = 50 + 0.4 * np.sin(i / 3.0)
        rows.append((c, c + 1.25, c - 1.25, c, 1_000_000))
    rows.append((58.0, 61.0, 57.0, 60.0, 4_000_000))     # gap day: close 60, high 61
    flag_low = 58.5 if fill_gap else 60.0                  # fill_gap -> dips below gap close
    for k in range(15):                                    # light-volume flag holding ~60.3
        c = 60.3 + 0.15 * np.sin(k / 2.0)
        rows.append((c, 61.0, flag_low, c, 600_000))
    rows.append((61.2, 62.5, 61.0, 62.0, 1_600_000))      # breakout: close 62 > flag high
    idx = pd.bdate_range(end="2026-06-05", periods=len(rows))
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=idx)
    gap_date = idx[40]                                     # the gap bar
    return df, gap_date


def test_detect_earnings_flag_positive():
    from pinpoint import patterns
    df, gap_date = _gap_flag_ohlcv(fill_gap=False)
    res = patterns.detect_earnings_flag(df, gap_date, gap_high=61.0)
    assert res["detected"] is True
    assert res["ema_zone"] in ("5", "10", "20")
    assert res["flag_days"] == 16
    assert res["breakout_volume_ratio"] > 1.5


def test_detect_earnings_flag_gap_fill_negative():
    from pinpoint import patterns
    df, gap_date = _gap_flag_ohlcv(fill_gap=True)     # flag dips below gap close
    res = patterns.detect_earnings_flag(df, gap_date, gap_high=61.0)
    assert res["detected"] is False                   # didn't hold the gap


def test_enrich_focus_earnings_flag_fires_and_boosts_score():
    from pinpoint import pipeline
    from pinpoint import regime as regime_mod
    df, gap_date = _gap_flag_ohlcv(fill_gap=False)
    universe = pd.DataFrame([{
        "ticker": "EFX", "company": "E", "sector": "Technology", "industry": "Software",
        "beta": 1.5, "sma20_pct": 3.0, "sma50_pct": 9.0, "sma200_pct": 22.0,
        "rel_volume": 3.0, "eps_this_y": 50.0, "pct_below_high": 2.0}])
    targets = pd.DataFrame([{
        "ticker": "EFX", "company": "E", "sector": "Technology", "price": 62.0,
        "rs": 95.0, "stage": "Stage 2 (advancing)", "growth": "EPS yr 50%", "theme": ""}])
    reg = regime_mod.from_fundaments(sample_data.regime_fixture())
    idx = sample_data.ohlcv_fixture("SPY", shape="index")
    ctx = {"EFX": {"gap_date": gap_date.date().isoformat(),
                   "initial_post_gap_high": 61.0, "gap_pct": 8.4}}

    with_flag = pipeline.enrich_focus(targets, universe, reg,
                                      ohlcv_provider=lambda t: df, index_daily=idx,
                                      earnings_ctx=ctx)
    assert len(with_flag) == 1
    row = with_flag.iloc[0]
    assert bool(row["earnings_flag_active"]) is True
    assert row["gap_pct"] == 8.4
    assert "Earnings flag breakout (+25 bonus, highest-edge)" in row["layers"]

    # heavy weighting on the 0-100 scale: the earnings-flag bonus is +25 (Phase
    # 10 step 1/2), unless the base already pushed the name to the 100 cap.
    without = pipeline.enrich_focus(targets, universe, reg,
                                    ohlcv_provider=lambda t: df, index_daily=idx,
                                    earnings_ctx={})
    assert len(without) == 1
    base = without.iloc[0]["pinpoint_score"]
    expected = min(100.0, base + 25.0) - base
    assert with_flag.iloc[0]["pinpoint_score"] - base == pytest.approx(expected, abs=1e-6)


def test_active_ctx():
    d0 = date(2026, 5, 1)
    ew.persist([{"ticker": "ABC", "gap": 9.0}], ohlcv_provider=_ohlcv, today=d0)
    ctx = ew.active_ctx(today=d0 + timedelta(days=14))
    assert "ABC" in ctx and ctx["ABC"]["gap_pct"] == 9.0
