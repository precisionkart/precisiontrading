"""Tests for the Watchlist v2 board: entries, history, paper trades, status."""

import pandas as pd
import pytest

from pinpoint import watchlist as wl
from datetime import date


@pytest.fixture(autouse=True)
def _tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(wl, "_path", lambda name: str(tmp_path / name))
    yield


def test_add_remove_toggle_and_metadata():
    wl.add("stm", today=date(2026, 6, 17))
    assert wl.is_member("STM")
    e = wl.load_entries()[0]
    assert e["ticker"] == "STM" and e["added_date"] == "2026-06-17" and e["notes"] == ""
    wl.add("STM")                                  # idempotent
    assert wl.tickers() == ["STM"]
    wl.remove("STM")
    assert not wl.is_member("STM")


def test_notes_capped_280():
    wl.add("AAA")
    wl.set_notes("AAA", "x" * 400)
    assert len(wl.load_entries()[0]["notes"]) == 280


def test_history_snapshot_idempotent_same_date():
    rows = [{"ticker": "STM", "price": 73.7, "rs": 97, "score": 75.6,
             "status": "ACTIVE", "pattern": "High & tight flag"}]
    wl.snapshot(rows, source="cron", today=date(2026, 6, 17))
    wl.snapshot(rows, source="manual", today=date(2026, 6, 17))   # same date again
    hist = wl.load_history()["STM"]["history"]
    assert len(hist) == 1                          # no duplicate for the same date
    assert hist[0]["source"] == "manual"           # latest write wins
    # a new date appends
    wl.snapshot([{"ticker": "STM", "price": 74.2, "rs": 97, "score": 76.1,
                  "status": "ACTIVE", "pattern": "High & tight flag"}], today=date(2026, 6, 18))
    assert len(wl.load_history()["STM"]["history"]) == 2
    assert wl.since_added_pct("STM") == pytest.approx(0.7, abs=0.1)   # 73.7 -> 74.2
    assert wl.rs_series("STM") == [97.0, 97.0]


def test_status_pill_computation():
    focus = pd.DataFrame([{"ticker": "STM", "reward_risk": 6.6},
                          {"ticker": "LOWRR", "reward_risk": 3.0}])
    targets = pd.DataFrame([{"ticker": "STM"}, {"ticker": "WATCHY"}, {"ticker": "LOWRR"}])
    ectx = {"GAPPER": {}}
    assert wl.compute_status("STM", focus, targets, ectx) == "ACTIVE"      # focus + rr>=5
    assert wl.compute_status("LOWRR", focus, targets, ectx) == "WATCH"     # focus but rr<5 -> not active
    assert wl.compute_status("GAPPER", focus, targets, ectx) == "EARNINGS"
    assert wl.compute_status("WATCHY", focus, targets, ectx) == "WATCH"
    assert wl.compute_status("NOWHERE", focus, targets, ectx) == "DORMANT"


def test_dormant_streak():
    h = {"date_seed": 1}
    for i, d in enumerate(["2026-06-10", "2026-06-11", "2026-06-12"]):
        wl.snapshot([{"ticker": "Z", "price": 10, "rs": 50, "score": 40,
                      "status": "DORMANT", "pattern": "—"}], today=date.fromisoformat(d))
    assert wl.dormant_streak("Z") == 3
    wl.snapshot([{"ticker": "Z", "price": 11, "rs": 60, "score": 55,
                  "status": "WATCH", "pattern": "Base"}], today=date(2026, 6, 13))
    assert wl.dormant_streak("Z") == 0             # streak broken by a non-dormant day


def _ohlcv(high, low):
    return pd.DataFrame([{"Open": 100, "High": high, "Low": low, "Close": 100, "Volume": 1e6}])


def test_paper_trade_win_resolution_idempotent():
    wl.mark_paper_trade("STM", entry=81.43, stop=73.19, target=135.57,
                        reward_risk=6.6, shares=6, today=date(2026, 6, 17))
    tr = wl.load_trades()[0]
    assert tr["status"] == "open" and tr["risk_dollars"] == pytest.approx(49.44, abs=0.01)
    # target hit today
    wl.resolve_trades(lambda tk: _ohlcv(high=136.0, low=120.0), today=date(2026, 6, 18))
    tr = wl.load_trades()[0]
    assert tr["status"] == "won" and tr["exit_reason"] == "target_hit"
    assert tr["r_achieved"] == 6.6 and tr["exit_price"] == 135.57
    # idempotent: re-running doesn't change a resolved trade
    wl.resolve_trades(lambda tk: _ohlcv(high=200.0, low=10.0), today=date(2026, 6, 19))
    assert wl.load_trades()[0]["r_achieved"] == 6.6


def test_paper_trade_loss_resolution():
    wl.mark_paper_trade("BAD", entry=50.0, stop=47.0, target=70.0,
                        reward_risk=6.0, shares=10, today=date(2026, 6, 17))
    wl.resolve_trades(lambda tk: _ohlcv(high=52.0, low=46.5), today=date(2026, 6, 18))
    tr = wl.load_trades()[0]
    assert tr["status"] == "lost" and tr["exit_reason"] == "stop_hit" and tr["r_achieved"] == -1.0


def test_paper_stats():
    wl.save_trades([
        {"status": "won", "r_achieved": 6.6}, {"status": "won", "r_achieved": 5.0},
        {"status": "lost", "r_achieved": -1.0}, {"status": "open"},
    ])
    s = wl.paper_stats()
    assert s["total"] == 4 and s["won"] == 2 and s["lost"] == 1 and s["open"] == 1
    assert s["win_rate"] == pytest.approx(66.7, abs=0.1)
    assert s["avg_r"] == pytest.approx(3.53, abs=0.01)
