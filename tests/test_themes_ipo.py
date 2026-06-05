"""Tests for themes, ipo, normalize_quote, and theme/ipo scoring layers (offline)."""

import pandas as pd

from pinpoint import sample_data
from pinpoint import pipeline
from pinpoint import regime as regime_mod
from pinpoint.themes import ThemeContext
from pinpoint import ipo as ipo_mod


# --- normalize_quote (reused by the My-Picks analyzer) ---------------------
def test_normalize_quote_builds_row():
    fund = {
        "Company": "Apple Inc", "Sector": "Technology", "Industry": "Consumer Electronics",
        "Price": "307.34", "Avg Volume": "44.65M", "Rel Volume": "1.45", "Beta": "1.09",
        "SMA20": "1.01%", "SMA50": "9.28%", "SMA200": "15.89%",
        "52W High": "316.94 -3.03%",
        "EPS this Y": "17.31%", "EPS Q/Q": "22.04%", "Sales Q/Q": "16.60%",
        "Perf Month": "6.90%", "Perf Quarter": "19.37%", "Perf Half Y": "8.16%",
        "Perf Year": "53.19%",
    }
    row = pipeline.normalize_quote("AAPL", fund)
    assert row["ticker"] == "AAPL"
    assert row["sector"] == "Technology"
    assert abs(row["price"] - 307.34) < 1e-6
    assert abs(row["avg_volume"] - 44_650_000) < 1
    assert abs(row["sma20_pct"] - 1.01) < 1e-6
    assert abs(row["pct_below_high"] - 3.03) < 1e-6     # from "316.94 -3.03%"
    assert abs(row["eps_qoq"] - 22.04) < 1e-6
    assert abs(row["sales_qoq"] - 16.60) < 1e-6


# --- ThemeContext mapping + predicates -------------------------------------
def _ctx():
    return ThemeContext(
        theme_rank={"Semiconductors": {"rank": 1, "pct": 1.0, "score": 30.0},
                    "Technology": {"rank": 2, "pct": 0.8, "score": 20.0},
                    "Healthcare": {"rank": 9, "pct": 0.2, "score": 2.0}},
        industry_rank={"Semiconductors": {"rank": 1, "pct": 0.99, "score": 40.0},
                       "Consumer Electronics": {"rank": 80, "pct": 0.4, "score": 5.0}},
        n_themes=3, n_industries=2)


def test_theme_for_industry_keyword_wins():
    c = _ctx()
    assert c.theme_for("Technology", "Semiconductors") == "Semiconductors"
    assert c.theme_for("Technology", "Software - Infrastructure") == "Technology"


def test_is_hot_theme_top_30pct():
    c = _ctx()
    assert c.is_hot_theme("Technology", "Semiconductors")     # pct 1.0 -> hot
    assert c.is_hot_theme("Technology", "Software")            # Technology pct 0.8 -> hot
    assert not c.is_hot_theme("Healthcare", "Drug")            # pct 0.2 -> not


def test_is_top_industry_top_10pct():
    c = _ctx()
    assert c.is_top_industry("Semiconductors")               # pct 0.99
    assert not c.is_top_industry("Consumer Electronics")     # pct 0.4


# --- snapshot_layers fires theme/ipo layers when contexts supplied ---------
def test_snapshot_layers_theme_and_ipo():
    reg = regime_mod.from_fundaments(sample_data.regime_fixture())
    row = pd.Series({"ticker": "NVDA", "sector": "Technology", "industry": "Semiconductors",
                     "sma20_pct": 2.0, "sma50_pct": 9.0, "sma200_pct": 22.0,
                     "rel_volume": 3.0, "eps_this_y": 50.0})
    layers = pipeline.snapshot_layers(row, reg, 99.0, theme_ctx=_ctx(),
                                      ipo_ctx={"NVDA": True})
    assert layers["hot_theme"]
    assert layers["top_industry_group"]
    assert layers["ipo_edge"]


# --- IPO initial-high + persistence ----------------------------------------
def test_initial_ipo_high_and_assess(tmp_path):
    daily = sample_data.ohlcv_fixture("RDDT")
    high, first_date = ipo_mod.initial_ipo_high(daily)
    assert high > 0 and first_date
    store = {}
    st = ipo_mod.assess_ipo("RDDT", "Technology", daily, store)
    assert st is not None
    assert "RDDT" in store
    # status reflects price vs the stored initial high
    assert st.status in ("BREAKING OUT (price discovery)", "approaching IPO high",
                         "below IPO high")


def test_ipo_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ipo_mod, "_store_path", lambda: str(tmp_path / "ipo_highs.json"))
    ipo_mod.save_store({"FOO": {"ipo_high": 50.0, "ipo_date": "2026-01-02"}})
    loaded = ipo_mod.load_store()
    assert loaded["FOO"]["ipo_high"] == 50.0


# --- rank_themes offline (stub OHLCV provider) -----------------------------
def test_rank_themes_offline():
    from pinpoint import themes
    from pinpoint.config import THEME_ETFS

    def provider(etf):
        # vary strength deterministically by ticker so themes get a clear order
        df = sample_data.ohlcv_fixture(etf, shape="index")
        scale = 1.0 + (sum(ord(c) for c in etf) % 5) * 0.1
        df = df.copy()
        for c in ("Open", "High", "Low", "Close"):
            df[c] = df[c] * scale
        return df

    theme_rank, warnings = themes.rank_themes(ohlcv_provider=provider)
    assert len(theme_rank) == len(THEME_ETFS)
    # every theme has a rank within [1, n] and a percentile in (0, 1]
    n = len(theme_rank)
    for rec in theme_rank.values():
        assert 1 <= rec["rank"] <= n
        assert 0.0 < rec["pct"] <= 1.0
    assert min(r["rank"] for r in theme_rank.values()) == 1


def test_log_theme_history_roundtrip(tmp_path, monkeypatch):
    import types
    from pinpoint import themes
    monkeypatch.setattr(themes, "CONFIG",
                        types.SimpleNamespace(paths=types.SimpleNamespace(data_dir=str(tmp_path))))
    path = themes.log_theme_history(_ctx(), when="2026-W23")
    assert path is not None
    import json
    data = json.load(open(path))
    assert "2026-W23" in data and "Semiconductors" in data["2026-W23"]
