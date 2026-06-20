"""ohlcv.py — OHLCV history, caching, and moving averages (spec Sections 3.5-3.8).

Source policy: **Massive (Polygon-compatible) primary, yfinance + Stooq
fallbacks.** Massive exposes a Polygon-compatible REST surface; we hit the daily
and weekly aggregate endpoints and the batch snapshot endpoint when an API key is
present (MASSIVE_API_KEY). When no key is set, or a call fails, we degrade to the
prior policy (yfinance primary, Stooq fallback) so the scanner keeps working
offline / on the free tier.

Provides:
  * fetch_daily(ticker)    — daily OHLCV (cached to data/ohlcv/<t>_1d.parquet)
  * fetch_weekly(ticker)   — weekly OHLCV (Massive /1/week, else resampled daily)
  * snapshot_prices(tks)   — batch current prices (Massive snapshot, else yfinance)
  * to_weekly(daily)       — weekly resample (W-FRI) for the dominant trend (3.8)
  * add_moving_averages()  — 5/10/20 EMA + 50/200 SMA (3.5)

Everything degrades gracefully: a fetch failure returns an empty frame and a
note rather than raising (Section 9). 65-minute intraday is not freely available
from these sources, so time-frame continuity (3.8) is approximated from
weekly+daily and that limitation is documented (timeframes.py).
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from .config import CONFIG

# Load .env if python-dotenv is available (best-effort; never fatal).
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass

logger = logging.getLogger("pinpoint.ohlcv")

OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

EMA_SPANS = (5, 10, 20)     # momentum / first support / the signal (3.5)
SMA_SPANS = (50, 200)       # intermediate / macro (3.5)

MASSIVE_API_KEY = os.environ.get("MASSIVE_API_KEY", "")
MASSIVE_BASE = "https://api.polygon.io"


# ---------------------------------------------------------------------------
# Cache helpers.
# ---------------------------------------------------------------------------
def _cache_dir() -> str:
    d = os.path.join(CONFIG.paths.data_dir, "ohlcv")
    os.makedirs(d, exist_ok=True)
    return d


def _cache_path(ticker: str, interval: str) -> str:
    safe = ticker.upper().replace("/", "-")
    return os.path.join(_cache_dir(), f"{safe}_{interval}.parquet")


def _read_cache(path: str, max_age_hours: float) -> Optional[pd.DataFrame]:
    if not os.path.exists(path):
        return None
    age_h = (datetime.now().timestamp() - os.path.getmtime(path)) / 3600.0
    if age_h > max_age_hours:
        return None
    try:
        df = pd.read_parquet(path)
        return df if _looks_ohlcv(df) else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("cache read failed for %s: %s", path, exc)
        return None


def _write_cache(path: str, df: pd.DataFrame) -> None:
    try:
        df.to_parquet(path)
    except Exception as exc:  # noqa: BLE001 — caching is best-effort
        logger.debug("cache write failed for %s: %s", path, exc)


def _looks_ohlcv(df: pd.DataFrame) -> bool:
    return df is not None and len(df) > 0 and all(c in df.columns for c in OHLCV_COLUMNS)


# ---------------------------------------------------------------------------
# Result container.
# ---------------------------------------------------------------------------
@dataclass
class OHLCVResult:
    df: pd.DataFrame
    source: str            # "cache" | "massive" | "yfinance" | "stooq" | "none"
    ok: bool
    note: str = ""

    @property
    def empty(self) -> bool:
        return self.df is None or len(self.df) == 0


# ---------------------------------------------------------------------------
# Sources.
# ---------------------------------------------------------------------------
def _massive_aggs(ticker: str, multiplier: int, timespan: str,
                  years: float = 2.0) -> pd.DataFrame:
    """Fetch aggregate bars from Massive's Polygon-compatible aggs endpoint.

    Returns an OHLCV frame indexed by date (ascending), or empty if no key /
    no data. `timespan` is "day" or "week"."""
    if not MASSIVE_API_KEY:
        return pd.DataFrame()
    import requests

    end = date.today()
    start = end - timedelta(days=int(365 * years) + 5)
    url = (f"{MASSIVE_BASE}/v2/aggs/ticker/{ticker.upper()}/range/"
           f"{multiplier}/{timespan}/{start.isoformat()}/{end.isoformat()}"
           f"?adjusted=true&sort=asc&limit=50000&apiKey={MASSIVE_API_KEY}")
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    results = payload.get("results") or []
    if not results:
        return pd.DataFrame()
    rows = []
    idx = []
    for r in results:
        if "t" not in r:
            continue
        idx.append(pd.to_datetime(r["t"], unit="ms"))
        rows.append({"Open": r.get("o"), "High": r.get("h"), "Low": r.get("l"),
                     "Close": r.get("c"), "Volume": r.get("v")})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(idx))
    df = df[OHLCV_COLUMNS].dropna(how="all").sort_index()
    return df


def _fetch_massive(ticker: str, years: float = 2.0) -> pd.DataFrame:
    return _massive_aggs(ticker, 1, "day", years=years)


def _fetch_yfinance(ticker: str, period: str = "2y") -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(ticker, period=period, interval="1d", auto_adjust=False,
                     progress=False, threads=False)
    if df is None or len(df) == 0:
        return pd.DataFrame()
    # yfinance may return a MultiIndex column frame for a single ticker.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns={c: c.title() for c in df.columns})
    keep = [c for c in OHLCV_COLUMNS if c in df.columns]
    df = df[keep].dropna(how="all")
    df.index = pd.to_datetime(df.index)
    return df


def _fetch_stooq(ticker: str) -> pd.DataFrame:
    """Stooq free daily CSV. US tickers use the '.us' suffix. Returns ascending
    by date."""
    import requests

    sym = ticker.lower().replace(".", "-")
    url = f"https://stooq.com/q/d/l/?s={sym}.us&i=d"
    resp = requests.get(url, timeout=15,
                        headers={"User-Agent": CONFIG.network.user_agent})
    resp.raise_for_status()
    text = resp.text.strip()
    if not text or text.lower().startswith("<"):
        return pd.DataFrame()
    df = pd.read_csv(io.StringIO(text))
    if "Date" not in df.columns or "Close" not in df.columns:
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    keep = [c for c in OHLCV_COLUMNS if c in df.columns]
    return df[keep].dropna(how="all")


# ---------------------------------------------------------------------------
# Public fetch.
# ---------------------------------------------------------------------------
def fetch_daily(ticker: str, period: str = "2y", use_cache: bool = True,
                max_age_hours: float = 18.0, cache_only: bool = False) -> OHLCVResult:
    """Daily OHLCV for `ticker`: cache -> Massive -> yfinance -> Stooq -> stale
    cache, in that order.

    `cache_only=True` (read-only cloud mode) never hits the network: it returns
    the cached frame if present (any age), else an empty result. The cloud app
    relies on the pre-committed OHLCV cache so it makes zero live calls."""
    path = _cache_path(ticker, "1d")
    if cache_only:
        if os.path.exists(path):
            try:
                df = pd.read_parquet(path)
                if _looks_ohlcv(df):
                    return OHLCVResult(df=df, source="cache", ok=True)
            except Exception:  # noqa: BLE001
                pass
        return OHLCVResult(df=pd.DataFrame(columns=OHLCV_COLUMNS), source="none",
                           ok=False, note=f"{ticker} not in committed cache")
    if use_cache:
        cached = _read_cache(path, max_age_hours)
        if cached is not None:
            return OHLCVResult(df=cached, source="cache", ok=True)

    # primary: Massive (Polygon-compatible), only when a key is configured
    if MASSIVE_API_KEY:
        try:
            years = 2.0
            try:
                if isinstance(period, str) and period.endswith("y"):
                    years = float(period[:-1])
            except Exception:  # noqa: BLE001
                pass
            df = _fetch_massive(ticker, years=years)
            if _looks_ohlcv(df):
                _write_cache(path, df)
                return OHLCVResult(df=df, source="massive", ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("massive failed for %s: %s", ticker, exc)

    # fallback: yfinance
    try:
        df = _fetch_yfinance(ticker, period=period)
        if _looks_ohlcv(df):
            _write_cache(path, df)
            return OHLCVResult(df=df, source="yfinance", ok=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance failed for %s: %s", ticker, exc)

    # fallback: Stooq
    try:
        df = _fetch_stooq(ticker)
        if _looks_ohlcv(df):
            _write_cache(path, df)
            return OHLCVResult(df=df, source="stooq", ok=True,
                               note="used Stooq fallback (massive/yfinance unavailable)")
    except Exception as exc:  # noqa: BLE001
        logger.warning("stooq failed for %s: %s", ticker, exc)

    # last resort: stale cache if any
    if os.path.exists(path):
        try:
            df = pd.read_parquet(path)
            if _looks_ohlcv(df):
                return OHLCVResult(df=df, source="cache", ok=True,
                                   note="served STALE cache (live fetch failed)")
        except Exception:  # noqa: BLE001
            pass

    return OHLCVResult(df=pd.DataFrame(columns=OHLCV_COLUMNS), source="none",
                       ok=False, note=f"no OHLCV available for {ticker}")


def fetch_weekly(ticker: str, period: str = "2y", use_cache: bool = True,
                 max_age_hours: float = 18.0, cache_only: bool = False) -> OHLCVResult:
    """Weekly OHLCV for `ticker`: cache -> Massive (/1/week) -> resampled daily.

    Falls back to resampling the daily series via to_weekly() whenever the
    Massive weekly endpoint is unavailable, errors, or returns nothing."""
    path = _cache_path(ticker, "1w")
    if cache_only:
        if os.path.exists(path):
            try:
                df = pd.read_parquet(path)
                if _looks_ohlcv(df):
                    return OHLCVResult(df=df, source="cache", ok=True)
            except Exception:  # noqa: BLE001
                pass
        # derive from cached daily without any network call
        daily = fetch_daily(ticker, cache_only=True)
        if not daily.empty:
            wk = to_weekly(daily.df)
            if _looks_ohlcv(wk):
                return OHLCVResult(df=wk, source="resampled", ok=True,
                                   note="weekly resampled from cached daily")
        return OHLCVResult(df=pd.DataFrame(columns=OHLCV_COLUMNS), source="none",
                           ok=False, note=f"{ticker} weekly not in committed cache")

    if use_cache:
        cached = _read_cache(path, max_age_hours)
        if cached is not None:
            return OHLCVResult(df=cached, source="cache", ok=True)

    # primary: Massive weekly aggregates
    if MASSIVE_API_KEY:
        try:
            years = 2.0
            try:
                if isinstance(period, str) and period.endswith("y"):
                    years = float(period[:-1])
            except Exception:  # noqa: BLE001
                pass
            df = _massive_aggs(ticker, 1, "week", years=years)
            if _looks_ohlcv(df):
                _write_cache(path, df)
                return OHLCVResult(df=df, source="massive", ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("massive weekly failed for %s: %s", ticker, exc)

    # fallback: resample the daily series
    daily = fetch_daily(ticker, period=period, use_cache=use_cache,
                        max_age_hours=max_age_hours)
    if not daily.empty:
        wk = to_weekly(daily.df)
        if _looks_ohlcv(wk):
            _write_cache(path, wk)
            return OHLCVResult(df=wk, source="resampled", ok=True,
                               note="weekly resampled from daily")

    return OHLCVResult(df=pd.DataFrame(columns=OHLCV_COLUMNS), source="none",
                       ok=False, note=f"no weekly OHLCV available for {ticker}")


def snapshot_prices(tickers: list[str]) -> dict[str, float]:
    """Batch current prices for `tickers` in a single Massive snapshot call,
    falling back to yfinance per-ticker for any name not returned.

    Returns {TICKER: price}. Missing/unavailable names are simply absent."""
    out: dict[str, float] = {}
    tickers = [str(t).upper().strip() for t in tickers if str(t).strip()]
    if not tickers:
        return out

    if MASSIVE_API_KEY:
        try:
            import requests
            joined = ",".join(tickers)
            url = (f"{MASSIVE_BASE}/v2/snapshot/locale/us/markets/stocks/tickers"
                   f"?tickers={joined}&apiKey={MASSIVE_API_KEY}")
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            payload = resp.json()
            for row in (payload.get("tickers") or []):
                tk = str(row.get("ticker", "")).upper()
                if not tk:
                    continue
                price = None
                lt = row.get("lastTrade") or {}
                if isinstance(lt, dict) and lt.get("p"):
                    price = lt.get("p")
                if price is None:
                    day = row.get("day") or {}
                    if isinstance(day, dict) and day.get("c"):
                        price = day.get("c")
                if price is None:
                    mn = row.get("min") or {}
                    if isinstance(mn, dict) and mn.get("c"):
                        price = mn.get("c")
                if price is None:
                    pd_ = row.get("prevDay") or {}
                    if isinstance(pd_, dict) and pd_.get("c"):
                        price = pd_.get("c")
                if price is not None:
                    try:
                        out[tk] = float(price)
                    except (TypeError, ValueError):
                        pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("massive snapshot failed: %s", exc)

    # fallback: yfinance for any missing names
    missing = [t for t in tickers if t not in out]
    if missing:
        try:
            import yfinance as yf
            data = yf.download(missing, period="2d", interval="1d",
                               auto_adjust=False, progress=False, threads=False,
                               group_by="ticker")
            for t in missing:
                try:
                    if len(missing) == 1:
                        close = data["Close"]
                    else:
                        close = data[t]["Close"]
                    close = close.dropna()
                    if len(close):
                        out[t] = float(close.iloc[-1])
                except Exception:  # noqa: BLE001
                    continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("yfinance snapshot fallback failed: %s", exc)

    return out


# ---------------------------------------------------------------------------
# Transforms.
# ---------------------------------------------------------------------------
def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV to weekly bars (week ending Friday) for the dominant
    trend / Stage-1 base read (3.8)."""
    if daily is None or len(daily) == 0:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last",
           "Volume": "sum"}
    use = {k: v for k, v in agg.items() if k in daily.columns}
    weekly = daily.resample("W-FRI").agg(use).dropna(how="all")
    return weekly


def add_moving_averages(df: pd.DataFrame, ema_spans=EMA_SPANS,
                        sma_spans=SMA_SPANS) -> pd.DataFrame:
    """Add 5/10/20 EMA and 50/200 SMA columns (3.5). EMAs use adjust=False so
    they match a trader's charting platform."""
    if df is None or len(df) == 0 or "Close" not in df.columns:
        return df
    out = df.copy()
    close = out["Close"]
    for s in ema_spans:
        out[f"EMA{s}"] = close.ewm(span=s, adjust=False).mean()
    for s in sma_spans:
        out[f"SMA{s}"] = close.rolling(s, min_periods=1).mean()
    return out


def emas_converged(df: pd.DataFrame, spans=EMA_SPANS, threshold_pct: float = 2.0,
                   lookback: int = 1) -> bool:
    """True if the EMAs are tightly converged (a contraction signal, 3.6/3.7):
    the spread between the highest and lowest of the given EMAs is within
    `threshold_pct`% of price, on the latest bar."""
    cols = [f"EMA{s}" for s in spans]
    if df is None or len(df) == 0 or not all(c in df.columns for c in cols):
        return False
    last = df.iloc[-1]
    vals = np.array([last[c] for c in cols], dtype="float64")
    if np.isnan(vals).any() or last["Close"] <= 0:
        return False
    spread = (vals.max() - vals.min()) / last["Close"] * 100.0
    return bool(spread <= threshold_pct)
