"""screener.py — the universe builder (replaces Finviz's screener).

Finviz used to hand us a pre-filtered, pre-computed leaders list. Massive gives
raw market data, so we reproduce that screening here:

  build_universe_df       — all active US stocks -> batch snapshots -> price/volume
                            filter -> a few hundred liquid names.
  enrich_with_ohlcv       — top-N by volume: OHLCV-derived SMA%/perf/ATR/RVOL/
                            pct_below_high (what Finviz pre-computed).
  enrich_with_fundamentals — per-name sector/industry/company + EPS/Sales growth.
  apply_universe_gates    — the §3.3 hard gates, sorted by quarter performance.

The output frame's columns are IDENTICAL to pipeline.normalize_universe so the
existing scoring/pattern/RS code consumes it unchanged.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from .config import CONFIG
from . import ohlcv as ohlcv_mod

logger = logging.getLogger("pinpoint.screener")

# The canonical normalized columns (must match pipeline.normalize_universe).
NORMALIZED_COLUMNS = [
    "ticker", "company", "sector", "industry", "price", "avg_volume",
    "rel_volume", "beta", "atr", "rsi", "gap", "sma20_pct", "sma50_pct",
    "sma200_pct", "pct_below_high", "perf_week", "perf_month", "perf_quarter",
    "perf_half", "perf_year", "change", "market_cap", "eps_this_y",
    "eps_past5y", "sales_past5y",
]


def _empty_universe() -> pd.DataFrame:
    return pd.DataFrame(columns=NORMALIZED_COLUMNS)


def _etf_exclusions() -> set:
    """Index/sector/theme ETFs we cache for regime & ranking but must NOT treat
    as scannable stocks in the weekend cache path."""
    ex = set(CONFIG.regime.benchmarks) | {"SPY", "QQQ", "IWM", "DIA"}
    try:
        from .config import THEME_ETFS
        from .themes import SECTOR_ETFS
        for v in THEME_ETFS.values():
            ex |= set(v)
        ex |= set(SECTOR_ETFS.values())
    except Exception:  # noqa: BLE001
        pass
    return ex


def _cached_tickers() -> list[str]:
    """Tickers with a committed daily OHLCV cache (data/ohlcv/<t>_1d.parquet)."""
    import glob
    cache_dir = ohlcv_mod._cache_dir()
    out = []
    for path in glob.glob(os.path.join(cache_dir, "*_1d.parquet")):
        stem = os.path.basename(path)[:-len("_1d.parquet")]
        if stem:
            out.append(stem.upper())
    return sorted(set(out))


def _build_universe_from_cache(min_price: float, min_avg_volume: int) -> pd.DataFrame:
    """Weekend / market-closed path: build the universe from the OHLCV cache
    (Friday's actual close), with NO live API calls. Price/volume/rel_volume come
    from the last cached bar + 20-day average; technicals are filled by
    enrich_with_ohlcv (also cache-only)."""
    exclude = _etf_exclusions()
    tickers = [t for t in _cached_tickers() if t not in exclude]
    rows = []
    for tk in tickers:
        res = ohlcv_mod.fetch_daily(tk, cache_only=True)
        if res.empty or len(res.df) < 20:
            continue
        d = res.df
        price = float(d["Close"].iloc[-1])
        last_vol = float(d["Volume"].iloc[-1])
        avg20 = float(d["Volume"].iloc[-20:].mean())
        if not (price == price and price > min_price):
            continue
        if not (avg20 == avg20 and avg20 >= min_avg_volume):
            continue
        rows.append({
            "ticker": tk, "price": price,
            "avg_volume": avg20, "volume": last_vol,
            "rel_volume": round(last_vol / avg20, 2) if avg20 else np.nan,
            "gap": np.nan, "change": np.nan,        # no live snapshot off-hours
            "company": np.nan, "sector": np.nan, "industry": np.nan,
            "beta": np.nan, "atr": np.nan, "rsi": np.nan,
            "sma20_pct": np.nan, "sma50_pct": np.nan, "sma200_pct": np.nan,
            "pct_below_high": np.nan, "perf_week": np.nan, "perf_month": np.nan,
            "perf_quarter": np.nan, "perf_half": np.nan, "perf_year": np.nan,
            "market_cap": np.nan, "eps_this_y": np.nan, "eps_past5y": np.nan,
            "sales_past5y": np.nan,
        })
    df = pd.DataFrame(rows, columns=None if rows else NORMALIZED_COLUMNS + ["volume"])
    logger.info("build_universe_df (CACHE/weekend): %d names from %d cached tickers "
                "(price>$%.0f & 20d-avg-vol>=%s)", len(df), len(tickers),
                min_price, f"{min_avg_volume:,}")
    return df


def build_universe_df(client, min_price: float = 10.0,
                      min_avg_volume: int = 300_000) -> pd.DataFrame:
    """All active US stocks -> batch snapshots -> price/volume filter.

    Per-ticker fields that need OHLCV or reference calls (perf_*, sma*_pct, EPS/
    Sales, sector/industry/company, beta, pct_below_high) are left as NaN here
    and filled by enrich_with_ohlcv / enrich_with_fundamentals.

    When the market is CLOSED, skip live snapshots entirely and build from the
    OHLCV cache (Friday's EOD close) — faster and cleaner than stale snapshots."""
    from .config import market_is_open
    if not market_is_open():
        return _build_universe_from_cache(min_price, min_avg_volume)

    tickers = client.get_universe(min_price=min_price, min_avg_volume=min_avg_volume)
    if not tickers:
        logger.warning("Massive universe returned no tickers")
        return _empty_universe()
    snaps = client.get_snapshots_batch(tickers)

    rows = []
    for tk in tickers:
        s = snaps.get(tk)
        if not s:
            continue
        price = s.get("price", np.nan)
        # snapshot 'volume' is prevDay volume off-hours — a usable liquidity proxy.
        volume = s.get("volume", np.nan)
        if not (price == price and price > min_price):
            continue
        if not (volume == volume and volume > min_avg_volume):
            continue
        rows.append({
            "ticker": tk, "price": float(price),
            "avg_volume": float(volume),          # provisional; refined from OHLCV
            "rel_volume": np.nan, "volume": float(volume),
            "gap": s.get("gap_pct", np.nan),
            "change": s.get("change_pct", np.nan),
            "company": np.nan, "sector": np.nan, "industry": np.nan,
            "beta": np.nan, "atr": np.nan, "rsi": np.nan,
            "sma20_pct": np.nan, "sma50_pct": np.nan, "sma200_pct": np.nan,
            "pct_below_high": np.nan, "perf_week": np.nan, "perf_month": np.nan,
            "perf_quarter": np.nan, "perf_half": np.nan, "perf_year": np.nan,
            "market_cap": np.nan, "eps_this_y": np.nan, "eps_past5y": np.nan,
            "sales_past5y": np.nan,
        })
    df = pd.DataFrame(rows)
    logger.info("build_universe_df: %d names pass price>$%.0f & volume>%s",
                len(df), min_price, f"{min_avg_volume:,}")
    return df


def _ohlcv_metrics(df: pd.DataFrame) -> dict:
    """Compute the Finviz-equivalent technicals from a daily OHLCV frame."""
    d = ohlcv_mod.add_moving_averages(df)
    close = d["Close"]
    last = float(close.iloc[-1])
    out: dict = {}

    def dist(col):
        if col in d.columns and d[col].iloc[-1] == d[col].iloc[-1] and last:
            return (last - float(d[col].iloc[-1])) / float(d[col].iloc[-1]) * 100.0
        return np.nan

    out["sma20_pct"] = dist("EMA20")      # 20-EMA distance (≈ Finviz SMA20 role)
    out["sma50_pct"] = dist("SMA50")
    out["sma200_pct"] = dist("SMA200")

    def perf(n):
        return (last / float(close.iloc[-n]) - 1.0) * 100.0 if len(close) > n else np.nan

    out["perf_week"] = perf(5)
    out["perf_month"] = perf(21)
    out["perf_quarter"] = perf(63)
    out["perf_half"] = perf(126)
    out["perf_year"] = perf(252)

    vol = float(d["Volume"].iloc[-1])
    avg20 = float(d["Volume"].iloc[-20:].mean())
    out["avg_volume"] = avg20
    out["rel_volume"] = round(vol / avg20, 2) if avg20 else np.nan

    window = close.iloc[-252:] if len(close) >= 2 else close
    hi = float(window.max())
    out["pct_below_high"] = max(0.0, (hi - last) / hi * 100.0) if hi else np.nan

    h, l, c = d["High"], d["Low"], d["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    out["atr"] = float(tr.rolling(14).mean().iloc[-1]) if len(d) > 14 else np.nan
    return out


def enrich_with_ohlcv(universe_df: pd.DataFrame, client,
                      top_n: int = 500) -> pd.DataFrame:
    """Sort by volume desc, take top_n, fetch OHLCV (threaded) and fill the
    technical columns (SMA%/perf/ATR/RVOL/pct_below_high/avg_volume)."""
    if universe_df is None or len(universe_df) == 0:
        return _empty_universe()
    from .config import market_is_open
    cache_only = not market_is_open()        # weekend: pure cache reads, no API
    df = universe_df.sort_values("volume", ascending=False).head(top_n).copy()
    df = df.reset_index(drop=True)
    tickers = list(df["ticker"])

    def _one(tk):
        res = ohlcv_mod.fetch_daily(tk, cache_only=cache_only)
        if res.empty or len(res.df) < 30:
            return tk, None
        try:
            return tk, _ohlcv_metrics(res.df)
        except Exception as exc:  # noqa: BLE001
            logger.debug("ohlcv metrics failed for %s: %s", tk, exc)
            return tk, None

    metrics: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=20) as ex:
        for fut in as_completed([ex.submit(_one, t) for t in tickers]):
            tk, m = fut.result()
            if m is not None:
                metrics[tk] = m

    for col in ("sma20_pct", "sma50_pct", "sma200_pct", "perf_week", "perf_month",
                "perf_quarter", "perf_half", "perf_year", "avg_volume",
                "rel_volume", "pct_below_high", "atr"):
        df[col] = df["ticker"].map(lambda t, c=col: metrics.get(t, {}).get(c, np.nan))
    # drop names we couldn't get OHLCV for (no technicals -> can't grade)
    df = df[df["ticker"].isin(metrics)].reset_index(drop=True)
    logger.info("enrich_with_ohlcv: %d/%d names had usable OHLCV", len(df), len(tickers))
    return df


def enrich_with_fundamentals(df: pd.DataFrame, client) -> pd.DataFrame:
    """Fill sector/industry/company/market_cap + EPS/Sales growth per name
    (threaded; financials endpoint is slower so max_workers=10)."""
    if df is None or len(df) == 0:
        return df
    df = df.copy()
    tickers = list(df["ticker"])

    def _one(tk):
        try:
            det = client.get_ticker_details(tk)
            fin = client.get_financials(tk)
            return tk, det, fin
        except Exception as exc:  # noqa: BLE001
            logger.debug("fundamentals failed for %s: %s", tk, exc)
            return tk, {}, {}

    det_by, fin_by = {}, {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for fut in as_completed([ex.submit(_one, t) for t in tickers]):
            tk, det, fin = fut.result()
            det_by[tk] = det or {}
            fin_by[tk] = fin or {}

    df["company"] = df["ticker"].map(lambda t: det_by.get(t, {}).get("company"))
    df["sector"] = df["ticker"].map(lambda t: det_by.get(t, {}).get("sector"))
    df["industry"] = df["ticker"].map(lambda t: det_by.get(t, {}).get("industry"))
    df["market_cap"] = df["ticker"].map(lambda t: det_by.get(t, {}).get("market_cap", np.nan))
    for col in ("eps_this_y", "eps_past5y", "sales_past5y"):
        df[col] = df["ticker"].map(lambda t, c=col: fin_by.get(t, {}).get(c, np.nan))
    # carry QoQ growth too (the most-weighted 3.4 driver) for downstream use.
    df["eps_qoq"] = df["ticker"].map(lambda t: fin_by.get(t, {}).get("eps_qoq", np.nan))
    df["sales_qoq"] = df["ticker"].map(lambda t: fin_by.get(t, {}).get("sales_qoq", np.nan))
    # latest filing date drives earnings detection (no Finviz earnings screener).
    df["latest_filing_date"] = df["ticker"].map(
        lambda t: fin_by.get(t, {}).get("latest_filing_date"))
    return df


def apply_universe_gates(df: pd.DataFrame, min_price: float = 10.0,
                         min_avg_volume: int = 300_000,
                         max_pct_below_high: float = 10.0,
                         require_above_sma200: bool = True,
                         min_rel_volume: float = 2.0,
                         relax_rvol=None) -> pd.DataFrame:
    """Apply the §3.3 hard universe gates and sort by quarter performance desc
    (the old Finviz 'Performance (Quarter)' sort).

    The relative-volume gate is conditional: enforced during the live session,
    relaxed on weekends / after-hours (when a snapshot's RVOL is naturally low and
    would gate out every name). `relax_rvol=None` auto-detects via market_is_open;
    pass True/False to force. Price / avg-volume / pct-below-high / SMA200 gates
    are ALWAYS active. Sets df.attrs['rvol_relaxed'] for the UI banner."""
    from .config import market_is_open
    if df is None or len(df) == 0:
        return _empty_universe()
    if relax_rvol is None:
        relax_rvol = not market_is_open()
    out = df.copy()
    mask = (out["price"] > min_price) & (out["avg_volume"] >= min_avg_volume)
    mask &= out["pct_below_high"] <= max_pct_below_high
    if require_above_sma200:
        mask &= out["sma200_pct"] > 0
    if not relax_rvol and "rel_volume" in out.columns:
        # rel_volume comes from OHLCV (enrich_with_ohlcv), NOT the stale snapshot.
        mask &= out["rel_volume"] >= min_rel_volume
    else:
        logger.info("RVOL gate relaxed (market closed / weekend scan)")
    out = out[mask.fillna(False)]
    if "perf_quarter" in out.columns:
        out = out.sort_values("perf_quarter", ascending=False, na_position="last")
    out = out.reset_index(drop=True)
    out.attrs["rvol_relaxed"] = bool(relax_rvol)
    return out
