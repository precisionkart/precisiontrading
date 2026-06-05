"""Tests for the My-Picks analyzer (offline: fake client + fixture OHLCV)."""

import pandas as pd

from pinpoint import sample_data, analyzer
from pinpoint import regime as regime_mod


class FakeClient:
    def __init__(self, funds):
        self.funds = funds

    def fetch_quote_fundament(self, ticker):
        return self.funds.get(ticker, {})


def _strong_fundament():
    return {
        "Company": "Strong Co", "Sector": "Technology", "Industry": "Software",
        "Price": "50.00", "Avg Volume": "5M", "Rel Volume": "3.0", "Beta": "1.8",
        "SMA20": "3%", "SMA50": "9%", "SMA200": "22%", "52W High": "55.00 -1.00%",
        "EPS this Y": "60%", "EPS Q/Q": "40%", "Sales Q/Q": "35%",
        "Perf Month": "20%", "Perf Quarter": "45%", "Perf Half Y": "80%", "Perf Year": "200%",
    }


def _weak_fundament():
    return {
        "Company": "Cheap Co", "Sector": "Energy", "Industry": "Oil & Gas",
        "Price": "6.00", "Avg Volume": "120K", "Rel Volume": "0.8", "Beta": "1.1",
        "SMA20": "-4%", "SMA50": "-2%", "SMA200": "3%", "52W High": "10.00 -40.00%",
        "Perf Month": "-5%", "Perf Quarter": "2%", "Perf Half Y": "4%", "Perf Year": "8%",
    }


def _reference_universe(n=40):
    # a weak reference so STRONG ranks at the top (RS high)
    return pd.DataFrame({
        "perf_week": [0.5] * n, "perf_month": [1.0] * n, "perf_quarter": [3.0] * n,
        "perf_half": [5.0] * n, "perf_year": [10.0] * n})


def _ohlcv(tk):
    # HTF fixture -> a measured pattern with large R:R for the strong name;
    # flat/declining for the weak name (no measured pattern).
    if tk == "STRONG":
        return sample_data.ohlcv_fixture("STRONG")          # high & tight flag
    n = 200
    flat = [10.0] * n
    return pd.DataFrame({"Open": flat, "High": [10.2] * n, "Low": [9.8] * n,
                         "Close": flat, "Volume": [5e5] * n},
                        index=pd.date_range(end="2026-06-05", periods=n, freq="B"))


def test_analyzer_a_plus_setup():
    reg = regime_mod.from_fundaments(sample_data.regime_fixture())   # bull
    client = FakeClient({"STRONG": _strong_fundament()})
    res = analyzer.analyze_picks(client, ["STRONG"], reg,
                                 reference_universe=_reference_universe(),
                                 index_daily=_ohlcv("IDX"), ohlcv_provider=_ohlcv)
    assert len(res) == 1
    pr = res[0]
    assert pr.classification == "A+"
    assert pr.is_pinpoint
    assert all(g.passed for g in pr.gates)
    assert pr.reward_risk is not None and pr.reward_risk >= 5.0
    assert "Pinpoint setup" in pr.verdict


def test_analyzer_fail_shows_reasons():
    reg = regime_mod.from_fundaments(sample_data.regime_fixture())
    client = FakeClient({"WEAK": _weak_fundament()})
    res = analyzer.analyze_picks(client, ["WEAK"], reg,
                                 reference_universe=_reference_universe(),
                                 index_daily=_ohlcv("IDX"), ohlcv_provider=_ohlcv)
    pr = res[0]
    assert pr.classification == "fail"
    # the failing hard gates are surfaced, not hidden
    failed = {g.name for g in pr.gates if not g.passed}
    assert "Price > $10" in failed
    assert "Near 52w high" in failed
    assert pr.verdict.startswith("Not a Pinpoint setup")


def test_analyzer_sorts_a_plus_first():
    reg = regime_mod.from_fundaments(sample_data.regime_fixture())
    client = FakeClient({"STRONG": _strong_fundament(), "WEAK": _weak_fundament()})
    res = analyzer.analyze_picks(client, ["WEAK", "STRONG"], reg,
                                 reference_universe=_reference_universe(),
                                 index_daily=_ohlcv("IDX"), ohlcv_provider=_ohlcv)
    assert [r.ticker for r in res] == ["STRONG", "WEAK"]
