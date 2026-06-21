"""massive_client.py — all market-data access via the Massive API.

Massive exposes a Polygon.io-compatible REST surface (base https://api.polygon.io).
This module REPLACES finviz_client.py as the live data source: the universe,
snapshots, OHLCV aggregates, reference details (sector via SIC), and quarterly
financials all come from Massive now. No Finviz / finvizfinance access remains.

Design:
  * MassiveClient owns the HTTP plumbing (key from MASSIVE_API_KEY, retry/backoff
    on 429/5xx, a small inter-call sleep, never-raise error handling).
  * The six granular methods the screener uses: get_universe, get_snapshots_batch,
    get_snapshot, get_aggs_batch, get_ticker_details, get_financials.
  * fetch_quote_fundament(ticker) is a COMPATIBILITY shim that returns a
    Finviz-quote-shaped dict (computed from OHLCV + financials + details) so the
    untouched analyzer / normalize_quote / regime code keeps working unchanged.

Everything degrades gracefully — empty dict/list/DataFrame on failure (Section 9).
"""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd

from .config import CONFIG
from .sic_sector import sic_to_sector, industry_from_description

# Load .env so MASSIVE_API_KEY is available even outside the Streamlit/CLI bootstrap.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass

logger = logging.getLogger("pinpoint.massive")

BASE_URL = "https://api.polygon.io"
_CALL_SLEEP_S = 0.1            # gentle inter-call spacing
_MAX_RETRIES = 3
_SNAPSHOT_BATCH = 250         # tickers per batch-snapshot call
_UNIVERSE_TTL_HOURS = 24.0

OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def _nan() -> float:
    return float("nan")


class MassiveClient:
    """Polygon-compatible market-data client. Construct freely (no network at
    construction time); the key is read from MASSIVE_API_KEY if not passed."""

    base_url = BASE_URL

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key or os.environ.get("MASSIVE_API_KEY", "")
        self._notes: list[str] = []
        if not self.api_key:
            logger.warning("MASSIVE_API_KEY is empty — live Massive calls will fail.")

    # -- low-level HTTP -----------------------------------------------------
    @property
    def notes(self) -> list[str]:
        return list(self._notes)

    def _get(self, url: str, label: str = "") -> Optional[dict]:
        """GET with retry/backoff on 429/5xx. Returns parsed JSON or None.

        `url` may already contain query params; the apiKey is appended here so
        callers never embed the secret. Never raises."""
        import requests

        sep = "&" if "?" in url else "?"
        full = f"{url}{sep}apiKey={self.api_key}"
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                time.sleep(_CALL_SLEEP_S)
                resp = requests.get(full, timeout=25)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (429, 500, 502, 503, 504):
                    wait = 2.0 ** (attempt - 1)
                    logger.warning("Massive %s on %s (attempt %d) — backing off %.0fs",
                                   resp.status_code, label or url, attempt, wait)
                    time.sleep(wait)
                    continue
                # 4xx other than 429 won't get better on retry.
                logger.warning("Massive %s on %s: %s", resp.status_code, label or url,
                               resp.text[:160])
                self._notes.append(f"Massive {resp.status_code} on {label or url}")
                return None
            except Exception as exc:  # noqa: BLE001
                logger.warning("Massive request error on %s (attempt %d): %s",
                               label or url, attempt, exc)
                if attempt < _MAX_RETRIES:
                    time.sleep(2.0 ** (attempt - 1))
        self._notes.append(f"Massive request failed: {label or url}")
        return None

    # -- 1. ticker details --------------------------------------------------
    def get_ticker_details(self, ticker: str) -> dict:
        """GET /v3/reference/tickers/{ticker} — company/sector/industry/mktcap.

        Sector is derived from sic_code via the SIC→Finviz mapping; industry is
        the title-cased sic_description. Empty dict on failure."""
        j = self._get(f"{BASE_URL}/v3/reference/tickers/{ticker.upper()}",
                      label=f"details {ticker}")
        res = (j or {}).get("results") or {}
        if not res:
            return {}
        sic = res.get("sic_code")
        return {
            "ticker": ticker.upper(),
            "company": res.get("name"),
            "sector": sic_to_sector(sic),
            "industry": industry_from_description(res.get("sic_description")),
            "sic_code": sic,
            "description": res.get("description"),
            "market_cap": res.get("market_cap"),
            "share_class_shares_outstanding": res.get("share_class_shares_outstanding"),
            "list_date": res.get("list_date"),
            "primary_exchange": res.get("primary_exchange"),
        }

    # -- 2. financials ------------------------------------------------------
    def get_financials(self, ticker: str) -> dict:
        """GET /vX/reference/financials (quarterly) — growth metrics.

        Computes, as float percentages (NaN when unavailable):
          eps_qoq, sales_qoq  — current quarter vs SAME quarter prior year (YoY)
          eps_this_y          — latest 4Q EPS sum vs prior 4Q EPS sum
          eps_past5y          — annualized EPS growth over available history
          sales_past5y        — annualized revenue growth over available history
        Also returns latest_filing_date / latest_period for earnings detection."""
        url = (f"{BASE_URL}/vX/reference/financials?ticker={ticker.upper()}"
               f"&timeframe=quarterly&limit=12&sort=period_of_report_date&order=desc")
        j = self._get(url, label=f"financials {ticker}")
        results = (j or {}).get("results") or []
        out = {"eps_qoq": _nan(), "sales_qoq": _nan(), "eps_this_y": _nan(),
               "eps_past5y": _nan(), "sales_past5y": _nan(),
               "net_margin": _nan(), "roe": _nan(), "margin_history": [],
               "latest_filing_date": None, "latest_period_end": None}
        if not results:
            return out

        def _val(row, field):
            inc = (row.get("financials") or {}).get("income_statement", {}) or {}
            v = (inc.get(field) or {}).get("value")
            try:
                return float(v)
            except (TypeError, ValueError):
                return _nan()

        # ordered newest-first; keep quarters with a fiscal_period like Q1..Q4
        q = [r for r in results if str(r.get("fiscal_period", "")).startswith("Q")]
        if not q:
            return out
        out["latest_filing_date"] = q[0].get("filing_date")
        out["latest_period_end"] = q[0].get("end_date")

        eps = [_val(r, "diluted_earnings_per_share") for r in q]
        rev = [_val(r, "revenues") for r in q]

        # Net margin & ROE on a TTM (trailing-4-quarter) basis — the conventional
        # figures. net_margin = sum(net_income, 4Q) / sum(revenue, 4Q); roe =
        # sum(net_income, 4Q) / latest quarter-end equity. Reads net_income from
        # the income statement and total equity from the balance sheet. Needs a
        # full 4 quarters (no partial sums); graceful NaN on any missing input;
        # TTM revenue sum <= 0 -> margin NaN, equity <= 0 -> ROE NaN.
        def _bs_val(row, field):
            bs = (row.get("financials") or {}).get("balance_sheet", {}) or {}
            v = (bs.get(field) or {}).get("value")
            try:
                return float(v)
            except (TypeError, ValueError):
                return _nan()

        ni = [_val(r, "net_income_loss") for r in q]
        if len(q) >= 4:
            ni4, rev4 = ni[0:4], rev[0:4]
            if all(v == v for v in ni4) and all(v == v for v in rev4):
                ni_ttm, rev_ttm = sum(ni4), sum(rev4)
                eq0 = _bs_val(q[0], "equity")
                if rev_ttm > 0:
                    out["net_margin"] = ni_ttm / rev_ttm * 100.0
                if eq0 == eq0 and eq0 > 0:
                    out["roe"] = ni_ttm / eq0 * 100.0

        # Per-quarter (single-quarter, NOT TTM) net-margin series, newest-first,
        # up to ~8 quarters — lets a later relative one-time-item test compare the
        # latest margin against the company's own trailing norm. A quarter with
        # missing/zero revenue stays in place as NaN so the series is time-ordered.
        def _q_margin(n, r):
            if n == n and r == r and r != 0:
                return n / r * 100.0
            return _nan()
        out["margin_history"] = [_q_margin(ni[i], rev[i]) for i in range(min(8, len(q)))]

        def _growth(cur, prior):
            if cur != cur or prior != prior or prior == 0:
                return _nan()
            return (cur - prior) / abs(prior) * 100.0

        # YoY: latest quarter vs the same quarter 4 rows back.
        if len(eps) >= 5:
            out["eps_qoq"] = _growth(eps[0], eps[4])
            out["sales_qoq"] = _growth(rev[0], rev[4])
        # this-year proxy: trailing-4Q sum vs the prior trailing-4Q sum.
        if len(eps) >= 8:
            cur_y = np.nansum(eps[0:4]); prv_y = np.nansum(eps[4:8])
            out["eps_this_y"] = _growth(cur_y, prv_y)
        # multi-year annualized growth over whatever history exists (>=8 quarters).
        if len(eps) >= 8:
            years = (len(eps) - 1) / 4.0
            out["eps_past5y"] = _annualized(eps[0], eps[-1], years)
            out["sales_past5y"] = _annualized(rev[0], rev[-1], years)
        return out

    # -- 3. single snapshot -------------------------------------------------
    def get_snapshot(self, ticker: str) -> dict:
        """GET /v2/snapshot/.../tickers/{ticker} — current/last price + gap."""
        j = self._get(f"{BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers/"
                      f"{ticker.upper()}", label=f"snapshot {ticker}")
        t = (j or {}).get("ticker") or {}
        return _parse_snapshot(t) if t else {}

    # -- 4. batch snapshots -------------------------------------------------
    def get_snapshots_batch(self, tickers: list[str]) -> dict[str, dict]:
        """GET batch snapshot for up to 250 tickers/call, looping for more.

        When the market is CLOSED, live snapshots are stale/incomplete (the 'day'
        block is zero), so we return an empty dict immediately — the weekend scan
        path builds from the OHLCV cache (Friday's EOD) instead."""
        from .config import market_is_open
        if not market_is_open():
            logger.info("Market closed — using OHLCV cache for weekend scan")
            return {}
        tickers = [str(t).upper().strip() for t in tickers if str(t).strip()]
        out: dict[str, dict] = {}
        for i in range(0, len(tickers), _SNAPSHOT_BATCH):
            chunk = tickers[i:i + _SNAPSHOT_BATCH]
            url = (f"{BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers"
                   f"?tickers={','.join(chunk)}")
            j = self._get(url, label=f"snapshot batch [{i}:{i+len(chunk)}]")
            for row in (j or {}).get("tickers", []) or []:
                tk = str(row.get("ticker", "")).upper()
                if tk:
                    out[tk] = _parse_snapshot(row)
        return out

    # -- 5. universe --------------------------------------------------------
    def get_universe(self, min_price: float = 10.0,
                     min_avg_volume: int = 300_000) -> list[str]:
        """All active US common-stock (CS) tickers on XNAS/XNYS, paginated.

        Filters out names containing '.' or '-' (ADRs / preferred / warrants /
        units). Cached to data/store/universe_cache.json with a 24h TTL so we
        don't re-walk the full list every scan. (min_price/min_avg_volume are
        applied later from snapshots in screener.build_universe_df.)"""
        cached = _read_universe_cache()
        if cached is not None:
            return cached

        tickers: list[str] = []
        url = (f"{BASE_URL}/v3/reference/tickers?market=stocks"
               f"&exchange=XNAS&active=true&type=CS&limit=1000")
        # Two passes (XNAS then XNYS) — Polygon's `exchange` takes one MIC.
        for exch in ("XNAS", "XNYS"):
            nxt = (f"{BASE_URL}/v3/reference/tickers?market=stocks"
                   f"&exchange={exch}&active=true&type=CS&limit=1000")
            pages = 0
            while nxt and pages < 30:
                j = self._get(nxt, label=f"universe {exch} p{pages}")
                if not j:
                    break
                for r in j.get("results", []) or []:
                    tk = str(r.get("ticker", "")).strip().upper()
                    if tk and "." not in tk and "-" not in tk:
                        tickers.append(tk)
                nxt = j.get("next_url")
                pages += 1
        tickers = sorted(set(tickers))
        if tickers:
            _write_universe_cache(tickers)
        return tickers

    # -- 6. batch aggregates ------------------------------------------------
    def get_aggs_batch(self, tickers: list[str],
                       period_days: int = 365) -> dict[str, pd.DataFrame]:
        """Daily OHLCV for many tickers, concurrently (max_workers=20). Returns
        {ticker: DataFrame[Open,High,Low,Close,Volume]} (DatetimeIndex). Tickers
        that fail are silently skipped."""
        tickers = [str(t).upper().strip() for t in tickers if str(t).strip()]
        end = date.today()
        start = end - timedelta(days=period_days)
        out: dict[str, pd.DataFrame] = {}

        def _one(tk: str):
            url = (f"{BASE_URL}/v2/aggs/ticker/{tk}/range/1/day/"
                   f"{start.isoformat()}/{end.isoformat()}"
                   f"?adjusted=true&sort=asc&limit=50000")
            j = self._get(url, label=f"aggs {tk}")
            return tk, _aggs_to_df(j)

        with ThreadPoolExecutor(max_workers=20) as ex:
            for fut in as_completed([ex.submit(_one, t) for t in tickers]):
                try:
                    tk, df = fut.result()
                    if df is not None and len(df):
                        out[tk] = df
                except Exception as exc:  # noqa: BLE001
                    logger.debug("aggs worker failed: %s", exc)
        return out

    # -- recent IPOs --------------------------------------------------------
    def get_recent_ipos(self, within_days: int = 365) -> list[dict]:
        """GET /vX/reference/ipos — IPOs that listed within `within_days`.

        Returns [{ticker, listing_date, issuer_name}] for already-listed names
        (ipo_status != 'pending'), excluding SPAC units/rights/warrants (tickers
        with a non-alpha character or a trailing 'U'/'W'/'R' unit suffix). Newest
        first. Empty list on failure."""
        since = (date.today() - timedelta(days=within_days)).isoformat()
        url = (f"{BASE_URL}/vX/reference/ipos?listing_date.gte={since}"
               f"&order=desc&sort=listing_date&limit=1000")
        out: list[dict] = []
        pages = 0
        while url and pages < 10:
            j = self._get(url, label=f"ipos p{pages}")
            if not j:
                break
            for r in j.get("results", []) or []:
                tk = str(r.get("ticker", "")).strip().upper()
                if not tk or not tk.isalpha():
                    continue
                if str(r.get("ipo_status", "")).lower() == "pending":
                    continue
                # crude unit/right/warrant filter (SPAC plumbing, not tradable IPOs)
                if len(tk) == 5 and tk[-1] in ("U", "W", "R"):
                    continue
                out.append({"ticker": tk, "listing_date": r.get("listing_date"),
                            "issuer_name": r.get("issuer_name")})
            nxt = j.get("next_url")
            url = nxt if nxt else None
            pages += 1
        # de-dupe keeping the newest listing per ticker
        seen, deduped = set(), []
        for r in out:
            if r["ticker"] not in seen:
                seen.add(r["ticker"]); deduped.append(r)
        return deduped

    # -- compatibility shim: Finviz-quote-shaped fundament ------------------
    def fetch_quote_fundament(self, ticker: str) -> dict[str, Any]:
        """Return a Finviz-quote-shaped fundament dict for `ticker`, computed
        from OHLCV (SMA/Perf/ATR/RSI/52W), financials (EPS/Sales), and details
        (Company/Sector/Industry). This keeps the untouched analyzer /
        normalize_quote / regime code working against the new data source.

        Percentage fields are emitted as '%'-suffixed STRINGS exactly like
        Finviz's quote endpoint, so pipeline.to_pct parses them without scaling.
        Empty dict on total failure."""
        from . import ohlcv as ohlcv_mod
        res = ohlcv_mod.fetch_daily(ticker)
        details = self.get_ticker_details(ticker)
        fin = self.get_financials(ticker)
        if res.empty and not details:
            return {}
        df = ohlcv_mod.add_moving_averages(res.df) if not res.empty else None
        snap = self.get_snapshot(ticker)
        fund = _fundament_from_ohlcv(df)
        fund["Company"] = details.get("company")
        fund["Sector"] = details.get("sector")
        fund["Industry"] = details.get("industry")
        if snap.get("gap_pct") == snap.get("gap_pct"):
            fund["Gap"] = f"{snap['gap_pct']:.2f}%"
        # growth fields as '%' strings (NaN -> '-')
        for k, key in (("eps_this_y", "EPS this Y"), ("eps_past5y", "EPS past 5Y"),
                       ("sales_past5y", "Sales past 5Y"), ("eps_qoq", "EPS Q/Q"),
                       ("sales_qoq", "Sales Q/Q")):
            v = fin.get(k)
            fund[key] = f"{v:.2f}%" if isinstance(v, float) and v == v else "-"
        return fund


# ---------------------------------------------------------------------------
# Module-level helpers.
# ---------------------------------------------------------------------------
def _annualized(latest: float, oldest: float, years: float) -> float:
    """Annualized % growth from oldest->latest over `years`. NaN if undefined or
    signs make a CAGR meaningless (negative base)."""
    if (latest != latest or oldest != oldest or years <= 0
            or oldest <= 0 or latest <= 0):
        return _nan()
    return ((latest / oldest) ** (1.0 / years) - 1.0) * 100.0


def _parse_snapshot(t: dict) -> dict:
    """Normalize a Polygon snapshot row. `day` is all-zeros outside RTH, so we
    fall back to min->prevDay for a usable EOD price."""
    day = t.get("day") or {}
    prev = t.get("prevDay") or {}
    mn = t.get("min") or {}
    lt = t.get("lastTrade") or {}

    def _first_pos(*vals):
        for v in vals:
            try:
                f = float(v)
                if f and f == f:
                    return f
            except (TypeError, ValueError):
                continue
        return _nan()

    price = _first_pos(lt.get("p"), day.get("c"), mn.get("c"), prev.get("c"))
    open_ = _first_pos(day.get("o"), mn.get("o"))
    prev_close = _first_pos(prev.get("c"))
    volume = _first_pos(day.get("v"), prev.get("v"))
    gap = _nan()
    if open_ == open_ and prev_close == prev_close and prev_close:
        gap = (open_ - prev_close) / prev_close * 100.0
    return {
        "ticker": str(t.get("ticker", "")).upper(),
        "price": price,
        "prev_close": prev_close,
        "open": open_,
        "volume": volume,
        "gap_pct": gap,
        "change_pct": t.get("todaysChangePerc"),
    }


def _aggs_to_df(j: Optional[dict]) -> pd.DataFrame:
    results = (j or {}).get("results") or []
    if not results:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    idx, rows = [], []
    for r in results:
        if "t" not in r:
            continue
        idx.append(pd.to_datetime(r["t"], unit="ms"))
        rows.append({"Open": r.get("o"), "High": r.get("h"), "Low": r.get("l"),
                     "Close": r.get("c"), "Volume": r.get("v")})
    if not rows:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))[OHLCV_COLUMNS].sort_index()


def _trailing_pct(close: pd.Series, n: int) -> float:
    if close is None or len(close) <= n:
        return _nan()
    return (close.iloc[-1] / close.iloc[-n] - 1.0) * 100.0


def _rsi(close: pd.Series, period: int = 14) -> float:
    if close is None or len(close) <= period:
        return _nan()
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = up.iloc[-1] / down.iloc[-1] if down.iloc[-1] else np.inf
    return float(100.0 - 100.0 / (1.0 + rs))


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    if df is None or len(df) <= period:
        return _nan()
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def _fundament_from_ohlcv(df: Optional[pd.DataFrame]) -> dict[str, Any]:
    """Build the OHLCV-derived part of a Finviz-quote-shaped fundament dict.
    Percentage fields are '%'-strings; price/volume are plain numbers."""
    if df is None or len(df) == 0 or "Close" not in df.columns:
        return {}
    close = df["Close"]
    last = float(close.iloc[-1])
    out: dict[str, Any] = {"Price": round(last, 2)}

    def sma_pct(col):
        if col in df.columns and df[col].iloc[-1] == df[col].iloc[-1] and last:
            return (last - float(df[col].iloc[-1])) / float(df[col].iloc[-1]) * 100.0
        return _nan()

    # NB: add_moving_averages emits EMA20 (not SMA20); regime treats SMA20≈20EMA.
    for col, key in (("EMA20", "SMA20"), ("SMA50", "SMA50"), ("SMA200", "SMA200")):
        v = sma_pct(col)
        if v == v:
            out[key] = f"{v:.2f}%"
    for n, key in ((5, "Perf Week"), (21, "Perf Month"), (63, "Perf Quarter"),
                   (126, "Perf Half Y"), (252, "Perf Year")):
        v = _trailing_pct(close, n)
        if v == v:
            out[key] = f"{v:.2f}%"
    # 52-week high distance, formatted "<high> <pct>%" like Finviz's quote.
    window = close.iloc[-252:] if len(close) >= 2 else close
    hi = float(window.max()) if len(window) else _nan()
    if hi == hi and hi:
        out["52W High"] = f"{hi:.2f} {(last - hi) / hi * 100.0:.2f}%"
    if len(df):
        vol = float(df["Volume"].iloc[-1])
        avg = float(df["Volume"].iloc[-20:].mean())
        out["Avg Volume"] = avg
        out["Rel Volume"] = round(vol / avg, 2) if avg else _nan()
    atr = _atr(df)
    if atr == atr:
        out["ATR"] = round(atr, 2)
    rsi = _rsi(close)
    if rsi == rsi:
        out["RSI"] = round(rsi, 1)
    return out


# ---------------------------------------------------------------------------
# Universe cache (24h TTL).
# ---------------------------------------------------------------------------
def _universe_cache_path() -> str:
    d = CONFIG.paths.store_dir
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "universe_cache.json")


def _read_universe_cache() -> Optional[list[str]]:
    path = _universe_cache_path()
    if not os.path.exists(path):
        return None
    try:
        age_h = (datetime.now().timestamp() - os.path.getmtime(path)) / 3600.0
        if age_h > _UNIVERSE_TTL_HOURS:
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        tickers = data.get("tickers") if isinstance(data, dict) else data
        return list(tickers) if tickers else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("universe cache read failed: %s", exc)
        return None


def _write_universe_cache(tickers: list[str]) -> None:
    path = _universe_cache_path()
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"as_of": datetime.now().isoformat(), "tickers": tickers}, f)
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("universe cache write failed: %s", exc)
