"""Tests for the risk-based position sizing math (Phase 11, book PDF-232-234)."""

import pytest

from pinpoint import position_sizing as ps


def test_compute_shares_basic():
    # $10k account, 0.5% risk = $50 budget; per-share risk $8.24 -> floor(50/8.24)=6
    assert ps.compute_shares(10_000, 0.5, 81.43, 73.19) == 6
    # $100k, 1% = $1000 budget; per-share risk $2.00 -> 500 shares
    assert ps.compute_shares(100_000, 1.0, 50.0, 48.0) == 500
    # tight stop -> more shares
    assert ps.compute_shares(10_000, 0.5, 100.0, 99.50) == 100  # $50 / $0.50


def test_compute_shares_entry_equals_stop_returns_zero():
    assert ps.compute_shares(10_000, 0.5, 50.0, 50.0) == 0


def test_compute_shares_invalid_inputs_return_zero():
    assert ps.compute_shares(0, 0.5, 50.0, 48.0) == 0          # no account
    assert ps.compute_shares(10_000, 0, 50.0, 48.0) == 0       # no risk
    assert ps.compute_shares(10_000, 0.5, None, 48.0) == 0     # missing entry
    assert ps.compute_shares(10_000, 0.5, float("nan"), 48.0) == 0


def test_compute_dollar_risk():
    assert ps.compute_dollar_risk(6, 81.43, 73.19) == pytest.approx(49.44, abs=0.01)
    assert ps.compute_dollar_risk(0, 81.43, 73.19) == 0.0


def test_portfolio_heat_and_status():
    acct = 10_000
    positions = [
        {"shares": 6, "entry": 81.43, "stop": 73.19},   # ~$49.44 risk
        {"shares": 100, "entry": 100.0, "stop": 99.50},  # $50 risk
        {"dollar_risk": 50.0},                            # precomputed
    ]
    heat = ps.compute_portfolio_heat(positions, acct)     # ~149.44 / 10000 = 1.49%
    assert heat == pytest.approx(1.49, abs=0.01)
    # capping behavior: green well under cap, amber 80-100%, red over
    assert ps.heat_status(1.49, 5.0) == "green"
    assert ps.heat_status(4.2, 5.0) == "amber"            # 84% of cap
    assert ps.heat_status(5.5, 5.0) == "red"              # over cap
    assert ps.heat_status(0.0, 5.0) == "green"


def test_portfolio_heat_empty():
    assert ps.compute_portfolio_heat([], 10_000) == 0.0
    assert ps.compute_portfolio_heat(None, 10_000) == 0.0
    assert ps.compute_portfolio_heat([{"dollar_risk": 50}], 0) == 0.0


def test_load_open_positions_missing_file(tmp_path, monkeypatch):
    # no paper_trades.json -> [] (the hook Watchlist v2 will populate)
    monkeypatch.setattr(ps, "_paper_trades_path", lambda: str(tmp_path / "paper_trades.json"))
    assert ps.load_open_positions() == []


def test_load_open_positions_filters_closed(tmp_path, monkeypatch):
    import json
    p = tmp_path / "paper_trades.json"
    p.write_text(json.dumps([
        {"ticker": "AAA", "status": "open", "shares": 6, "entry": 81.43, "stop": 73.19},
        {"ticker": "BBB", "status": "closed", "shares": 10, "entry": 50, "stop": 48},
        {"ticker": "CCC", "shares": 5, "entry": 20, "stop": 19},
    ]))
    monkeypatch.setattr(ps, "_paper_trades_path", lambda: str(p))
    assert {x["ticker"] for x in ps.load_open_positions()} == {"AAA", "CCC"}
