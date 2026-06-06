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


def test_active_ctx():
    d0 = date(2026, 5, 1)
    ew.persist([{"ticker": "ABC", "gap": 9.0}], ohlcv_provider=_ohlcv, today=d0)
    ctx = ew.active_ctx(today=d0 + timedelta(days=14))
    assert "ABC" in ctx and ctx["ABC"]["gap_pct"] == 9.0
