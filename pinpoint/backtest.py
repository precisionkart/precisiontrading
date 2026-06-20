"""backtest.py — walk-forward backtest of the Pinpoint strategy (no lookahead).

The engine pre-loads 2y of OHLCV per ticker, then walks forward one trading day
at a time. On each day it scans the universe using ONLY bars up to that day
(self._slice enforces this — the single most important guarantee here), reusing
the production pattern/entry/scoring modules unchanged (patterns.best_pattern,
entries.compute_setup, scoring.score_layers). Signals are then simulated against
FUTURE bars with an explicit fill model and a priority-ordered exit model
(hard stop -> 5R target -> 10-EMA close trail -> max-hold -> end-of-data), under
a fixed-risk position-sizer and an 8-position concurrency cap.

Outputs: a results dict (+ JSON), a trades CSV, and a signals CSV under
data/backtest/. The Streamlit page (app/views/backtest.py) renders the results.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from .config import CONFIG
from . import ohlcv as ohlcv_mod
from . import patterns as patterns_mod
from . import entries as entries_mod
from . import scoring as scoring_mod

log = logging.getLogger("pinpoint.backtest")

BACKTEST_UNIVERSE = [
    # Semiconductors
    "NVDA", "AMD", "AVGO", "SMCI", "ARM", "MU", "AMAT", "LRCX", "MRVL", "QCOM",
    # AI/Software
    "CRWD", "DDOG", "NET", "SNOW", "NOW", "GTLB", "MDB", "PANW", "APP", "TTD",
    # Fintech/Crypto
    "HOOD", "COIN", "AFRM", "SOFI", "NU", "SQ",
    # Healthcare
    "LLY", "NVO", "ISRG", "DXCM", "HIMS",
    # Energy/Nuclear
    "VST", "CEG", "CCJ", "FSLR",
    # Consumer/Growth
    "SHOP", "MELI", "UBER", "CELH", "CAVA", "RDDT",
]

BENCHMARKS = ("SPY", "QQQ")
MIN_BARS = 60                      # minimum history before we scan a ticker
DEDUP_DAYS = 5                     # don't re-signal the same ticker within N days
MAX_FILL_DAYS = 3                  # buy-stop must trigger within N days or expire
MAX_HOLD_DAYS = 60                 # force exit after N trading days in a position
SCALE_OUT_GAIN_PCT = 15.0          # take partial profit when +15% from entry
SCALE_OUT_FRACTION = 2.0 / 3.0     # ...selling 2/3, leaving 1/3 as a 10-EMA runner


@dataclass
class Signal:
    """A pattern detected on a given date — not yet a trade."""
    ticker: str
    scan_date: str
    pattern: str
    entry: float
    stop: float
    risk: float
    target_3r: float
    target_5r: float
    measured_target: float
    reward_risk: float
    score: float
    regime: str
    price_at_signal: float


@dataclass
class Trade:
    """A Signal that got filled and has a result."""
    ticker: str
    scan_date: str
    entry_date: str
    pattern: str
    entry: float
    stop: float
    risk: float
    target_3r: float
    target_5r: float
    measured_target: float
    reward_risk: float
    score: float
    regime: str

    exit_date: str = ""
    exit_price: float = 0.0        # share-weighted average exit (incl. partial)
    exit_reason: str = ""          # STOP / SCALE+EMA10 / SCALE+STOP / EMA10_TRAIL / MAX_HOLD / END_OF_DATA
    r_multiple: float = 0.0        # blended across the scale-out + runner
    pnl_pct: float = 0.0
    win: bool = False
    shares: int = 0
    dollar_pnl: float = 0.0
    days_held: int = 0
    # Scale-out (2/3 off at +10%, 1/3 runner trailed on the 10-EMA close):
    scaled: bool = False
    scale_price: float = 0.0       # price the 2/3 came off at (+10% level / gap open)
    scale_date: str = ""
    scale_shares: int = 0
    runner_shares: int = 0
    runner_exit_price: float = 0.0
    runner_exit_reason: str = ""   # EMA10_TRAIL / STOP / MAX_HOLD / END_OF_DATA


class BacktestEngine:

    def __init__(self, start_date: str = "2025-12-01", end_date: str = "2026-03-01",
                 account_size: float = 100_000, risk_pct: float = 0.005,
                 universe: list | None = None, max_concurrent_positions: int = 8,
                 trail_ma: str | None = "EMA10", r_target: float | None = None,
                 atr_mult: float | None = None, scale_pct: float | None = None,
                 scale_frac: float = 2.0 / 3.0):
        self.start_date = pd.Timestamp(start_date)
        self.end_date = pd.Timestamp(end_date)
        self.account_size = account_size
        self.risk_pct = risk_pct
        self.universe = universe or BACKTEST_UNIVERSE
        self.max_positions = max_concurrent_positions
        # --- exit strategy (all configurable for sweeps) ---
        self.trail_ma = trail_ma          # MA column to trail on a close-below (or None)
        self.r_target = r_target          # full-exit fixed target in R (or None)
        self.atr_mult = atr_mult          # chandelier: exit when close < peak_close - mult*ATR
        self.scale_pct = scale_pct        # take `scale_frac` off at +scale_pct% (or None)
        self.scale_frac = scale_frac

        self.data: dict[str, pd.DataFrame] = {}
        self.trading_days: list[pd.Timestamp] = []
        self.signals: list[Signal] = []
        self.trades: list[Trade] = []

    # ------------------------------------------------------------------ data
    def load_data(self) -> None:
        """Pre-load OHLCV (+MAs) for every ticker + benchmarks. Builds the trading
        calendar from SPY's bars within [start, end]."""
        tickers = list(dict.fromkeys(list(self.universe) + list(BENCHMARKS)))

        def _one(tk):
            res = ohlcv_mod.fetch_daily(tk, use_cache=True, period="2y")
            if res.empty:
                log.warning("Loading %s... NO DATA", tk)
                return tk, pd.DataFrame()
            df = ohlcv_mod.add_moving_averages(res.df)
            df = df[~df.index.duplicated(keep="last")].sort_index()
            # ATR14 for the optional chandelier (ATR-trail) exit.
            h, l, c = df["High"], df["Low"], df["Close"]
            pc = c.shift(1)
            tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
            df["ATR14"] = tr.rolling(14).mean()
            log.info("Loading %s... done (%d bars)", tk, len(df))
            return tk, df

        with ThreadPoolExecutor(max_workers=10) as ex:
            for tk, df in ex.map(_one, tickers):
                if not df.empty:
                    self.data[tk] = df

        spy = self.data.get("SPY")
        if spy is None or spy.empty:
            raise RuntimeError("SPY data unavailable — cannot build trading calendar.")
        cal = spy.index[(spy.index >= self.start_date) & (spy.index <= self.end_date)]
        self.trading_days = list(cal)
        log.info("Backtest calendar: %d trading days (%s -> %s), %d tickers loaded",
                 len(self.trading_days),
                 self.trading_days[0].date() if self.trading_days else "?",
                 self.trading_days[-1].date() if self.trading_days else "?",
                 len([t for t in self.universe if t in self.data]))

    def _slice(self, ticker: str, as_of: pd.Timestamp) -> pd.DataFrame:
        """OHLCV strictly up to `as_of` — NO bar after as_of (zero lookahead).
        Returns empty if fewer than MIN_BARS bars exist."""
        df = self.data.get(ticker, pd.DataFrame())
        if df is None or df.empty:
            return pd.DataFrame()
        sliced = df[df.index <= as_of]
        return sliced if len(sliced) >= MIN_BARS else pd.DataFrame()

    def _regime_as_of(self, as_of: pd.Timestamp) -> str:
        """BULL/NEUTRAL/BEAR from SPY+QQQ point-in-time slices.
        BULL: both above 200 SMA AND above 20 EMA. NEUTRAL: above 200 but below
        20 EMA. BEAR: either below 200 SMA (or insufficient data)."""
        above200 = []
        above20 = []
        for sym in BENCHMARKS:
            s = self._slice(sym, as_of)
            if s.empty:
                return "BEAR"                      # be conservative when blind
            last = s.iloc[-1]
            c = float(last["Close"])
            sma200 = float(last.get("SMA200", np.nan))
            ema20 = float(last.get("EMA20", np.nan))
            if sma200 != sma200 or ema20 != ema20:
                return "BEAR"
            above200.append(c > sma200)
            above20.append(c > ema20)
        if not all(above200):
            return "BEAR"
        return "BULL" if all(above20) else "NEUTRAL"

    # -------------------------------------------------------------- scanning
    def _score(self, df: pd.DataFrame, setup, regime: str) -> float:
        """Lightweight point-in-time pinpoint_score from the production scorer."""
        last = df.iloc[-1]
        close = float(last["Close"])
        comp = patterns_mod.atr_compression(df)
        ema10 = float(last.get("EMA10", np.nan))
        ema20 = float(last.get("EMA20", np.nan))
        sma50 = float(last.get("SMA50", np.nan))
        sma200 = float(last.get("SMA200", np.nan))
        avg20v = float(df["Volume"].iloc[-20:].mean()) if len(df) >= 20 else np.nan
        layers = {
            "regime_bull": regime == "BULL",
            "valid_pattern": True,
            "reward_risk": bool(getattr(setup, "rr_ok", False)),
            "stage_2": bool(close > sma50 > sma200) if sma50 == sma50 and sma200 == sma200 else False,
            "correct_ma_reaction": bool(ema20 == ema20 and close >= ema20
                                        and (abs(close - ema10) / close <= 0.08 if ema10 == ema10 and close else False)),
            "volume_confirmation": bool(avg20v == avg20v and float(last["Volume"]) > avg20v),
            "chart_ok": True, "not_earnings_gap_down": True,
        }
        res = scoring_mod.score_layers(
            layers, partials={"tight_contraction": comp.get("compression_score", 0.0)})
        return float(res.score)

    def _scan_one_ticker(self, ticker: str, as_of: pd.Timestamp,
                         regime: str) -> "Signal | None":
        """Scan one ticker for one date. Returns a Signal or None. Never raises."""
        try:
            if regime == "BEAR":
                return None
            df = self._slice(ticker, as_of)
            if df.empty:
                return None
            pat = patterns_mod.best_pattern(df, finviz_signals=None, require_measured=True)
            if pat is None or pat.measured_target is None:
                return None
            setup = entries_mod.compute_setup(pat.trigger, pat.support_low, pat.measured_target)
            if not setup.rr_ok:
                return None
            return Signal(
                ticker=ticker, scan_date=as_of.strftime("%Y-%m-%d"),
                pattern=pat.name, entry=setup.entry, stop=setup.stop,
                risk=setup.risk, target_3r=setup.target_3r, target_5r=setup.target_5r,
                measured_target=float(pat.measured_target),
                reward_risk=float(setup.reward_risk or 0.0),
                score=self._score(df, setup, regime), regime=regime,
                price_at_signal=float(df["Close"].iloc[-1]))
        except Exception as exc:  # noqa: BLE001 — a bad ticker must never break the walk
            log.debug("scan failed %s @ %s: %s", ticker, as_of.date(), exc)
            return None

    def scan_all_dates(self) -> None:
        """Walk forward; scan every ticker each day; dedup repeat signals."""
        last_signal_day: dict[str, pd.Timestamp] = {}
        try:
            from tqdm import tqdm
            days = tqdm(self.trading_days, desc="scanning")
        except Exception:  # noqa: BLE001
            days = self.trading_days

        for as_of in days:
            regime = self._regime_as_of(as_of)
            if regime == "BEAR":
                continue
            with ThreadPoolExecutor(max_workers=8) as ex:
                found = list(ex.map(lambda tk: self._scan_one_ticker(tk, as_of, regime),
                                    self.universe))
            day_sigs = []
            for sig in found:
                if sig is None:
                    continue
                prev = last_signal_day.get(sig.ticker)
                if prev is not None and (as_of - prev).days <= DEDUP_DAYS:
                    continue                       # avoid re-entering the same setup
                last_signal_day[sig.ticker] = as_of
                day_sigs.append(sig)
                self.signals.append(sig)
            if day_sigs:
                desc = ", ".join(f"{s.ticker} {s.pattern}" for s in day_sigs[:6])
                log.info("%s: regime=%s, %d signals (%s)",
                         as_of.date(), regime, len(day_sigs), desc)

    # ------------------------------------------------------------ simulation
    def _future(self, ticker: str, after: pd.Timestamp) -> pd.DataFrame:
        df = self.data.get(ticker, pd.DataFrame())
        return df[df.index > after] if not df.empty else df

    def _simulate_one(self, sig: Signal) -> "Trade | None":
        """Simulate a single signal into a Trade (entry fill + exit), independent
        of other positions. Concurrency is applied later in simulate_trades."""
        scan_ts = pd.Timestamp(sig.scan_date)
        fut = self._future(sig.ticker, scan_ts)
        if fut.empty:
            return None

        # ---- ENTRY: buy-stop, must trigger within MAX_FILL_DAYS ----
        entry_bars = fut.iloc[:MAX_FILL_DAYS]
        actual_entry = None
        entry_idx = None
        for ts, bar in entry_bars.iterrows():
            if float(bar["High"]) >= sig.entry:
                # gap-up above the buy-stop fills at the open (worse for us)
                actual_entry = max(sig.entry, float(bar["Open"]))
                entry_idx = ts
                break
        if actual_entry is None:
            return None                            # never filled -> signal expired

        risk_ps = actual_entry - sig.stop
        if risk_ps <= 0:
            return None
        shares = max(1, int((self.account_size * self.risk_pct) / risk_ps))
        # Optional partial scale-out (disabled by default — see exit config).
        scale_on = self.scale_pct is not None
        scale_lvl = actual_entry * (1.0 + (self.scale_pct or 0) / 100.0)
        scale_shares = min(max(int(round(shares * self.scale_frac)), 0), shares) if scale_on else 0
        runner_shares = shares - scale_shares
        trail_col = self.trail_ma
        r_target_price = (actual_entry + self.r_target * risk_ps) if self.r_target else None

        # ---- EXIT (config-driven): per day, priority
        #   STOP > R-target > scale@+x% > ATR-chandelier(close) > MA-trail(close) > MAX_HOLD
        post = self.data[sig.ticker]
        post = post[post.index > entry_idx]
        scaled = False
        scale_price = 0.0
        scale_ts = None
        runner_exit_price = runner_exit_reason = exit_ts = None
        full_stop_price = full_stop_reason = None
        peak_close = actual_entry
        held = 0

        def _ma(bar):
            return float(bar.get(trail_col, np.nan)) if trail_col else np.nan

        def _chandelier_hit(close, bar):
            if self.atr_mult is None:
                return False
            atr = float(bar.get("ATR14", np.nan))
            return atr == atr and close < (peak_close - self.atr_mult * atr)

        for ts, bar in post.iterrows():
            held += 1
            open_ = float(bar["Open"]); high = float(bar["High"])
            low = float(bar["Low"]); close = float(bar["Close"])
            peak_close = max(peak_close, close)
            ma = _ma(bar)

            if not scaled:
                if low <= sig.stop:                                       # hard stop
                    full_stop_price, full_stop_reason, exit_ts = sig.stop, "STOP", ts
                    break
                if r_target_price is not None and high >= r_target_price:  # fixed R target
                    px = max(r_target_price, open_) if open_ >= r_target_price else r_target_price
                    full_stop_price, full_stop_reason, exit_ts = px, f"TARGET_{self.r_target:g}R", ts
                    break
                if scale_on and high >= scale_lvl:                        # partial scale-out
                    scaled = True
                    scale_price = max(scale_lvl, open_) if open_ >= scale_lvl else scale_lvl
                    scale_ts = ts
                    if runner_shares == 0:
                        runner_exit_price, runner_exit_reason, exit_ts = scale_price, "ALL_SCALED", ts
                        break
                    if (_chandelier_hit(close, bar)) or (ma == ma and close < ma):
                        runner_exit_price, runner_exit_reason, exit_ts = close, "TRAIL", ts
                        break
                    continue
                if _chandelier_hit(close, bar):                           # ATR chandelier
                    full_stop_price, full_stop_reason, exit_ts = close, "CHANDELIER", ts
                    break
                if ma == ma and close < ma:                              # MA-close trail
                    full_stop_price, full_stop_reason, exit_ts = close, "MA_TRAIL", ts
                    break
                if held >= MAX_HOLD_DAYS:
                    full_stop_price, full_stop_reason, exit_ts = close, "MAX_HOLD", ts
                    break
            else:                                                         # runner phase
                if low <= sig.stop:
                    runner_exit_price, runner_exit_reason, exit_ts = sig.stop, "STOP", ts
                    break
                if _chandelier_hit(close, bar) or (ma == ma and close < ma):
                    runner_exit_price, runner_exit_reason, exit_ts = close, "TRAIL", ts
                    break
                if held >= MAX_HOLD_DAYS:
                    runner_exit_price, runner_exit_reason, exit_ts = close, "MAX_HOLD", ts
                    break

        # resolve open positions that ran out of data
        if full_stop_price is None and runner_exit_price is None:
            if len(post) == 0:
                return None
            last_close = float(post.iloc[-1]["Close"])
            exit_ts = post.index[-1]
            held = len(post)
            if scaled:
                runner_exit_price, runner_exit_reason = last_close, "END_OF_DATA"
            else:
                full_stop_price, full_stop_reason = last_close, "END_OF_DATA"

        # ---- P&L: blended across the scale-out + runner ----
        if not scaled:                                           # whole position one exit
            ex = full_stop_price
            dollar = (ex - actual_entry) * shares
            wavg_exit = ex
            reason = full_stop_reason
        else:
            r_price = runner_exit_price if runner_exit_price is not None else scale_price
            dollar = (scale_price - actual_entry) * scale_shares \
                + (r_price - actual_entry) * runner_shares
            wavg_exit = (scale_price * scale_shares + r_price * runner_shares) / shares
            reason = ("SCALE+" + (runner_exit_reason or "")) if runner_shares else "ALL_SCALED"
        r_mult = dollar / (risk_ps * shares)

        return Trade(
            ticker=sig.ticker, scan_date=sig.scan_date,
            entry_date=entry_idx.strftime("%Y-%m-%d"), pattern=sig.pattern,
            entry=round(actual_entry, 2), stop=sig.stop, risk=round(risk_ps, 4),
            target_3r=sig.target_3r, target_5r=sig.target_5r,
            measured_target=sig.measured_target, reward_risk=sig.reward_risk,
            score=sig.score, regime=sig.regime,
            exit_date=exit_ts.strftime("%Y-%m-%d"), exit_price=round(wavg_exit, 2),
            exit_reason=reason, r_multiple=round(r_mult, 3),
            pnl_pct=round((wavg_exit - actual_entry) / actual_entry * 100, 3),
            win=bool(r_mult > 0), shares=shares, dollar_pnl=round(dollar, 2),
            days_held=int(held), scaled=scaled, scale_price=round(scale_price, 2) if scaled else 0.0,
            scale_date=scale_ts.strftime("%Y-%m-%d") if scale_ts is not None else "",
            scale_shares=scale_shares if scaled else 0,
            runner_shares=runner_shares if scaled else 0,
            runner_exit_price=round(runner_exit_price, 2) if (scaled and runner_exit_price is not None) else 0.0,
            runner_exit_reason=runner_exit_reason or "")

    def simulate_trades(self) -> None:
        """Build each signal's trade, then admit chronologically under the
        max-concurrent-position cap (signals arriving while 8 are open are
        skipped, reflecting real capital constraints)."""
        candidates = []
        for sig in self.signals:
            tr = self._simulate_one(sig)
            if tr is not None:
                candidates.append(tr)
        candidates.sort(key=lambda t: (t.entry_date, t.scan_date))

        open_until: list[pd.Timestamp] = []       # exit dates of currently-open positions
        for tr in candidates:
            entry_ts = pd.Timestamp(tr.entry_date)
            open_until = [d for d in open_until if d >= entry_ts]   # close finished ones
            if len(open_until) >= self.max_positions:
                continue                           # capital full -> skip this signal
            open_until.append(pd.Timestamp(tr.exit_date))
            self.trades.append(tr)

    # --------------------------------------------------------------- results
    def compute_results(self) -> dict:
        trades = self.trades
        n_sig = len(self.signals)
        n_tr = len(trades)
        rs = [t.r_multiple for t in trades]
        winners = [t for t in trades if t.win]
        losers = [t for t in trades if not t.win]

        def _mean(xs):
            return float(np.mean(xs)) if xs else 0.0

        # equity curve by exit date (running realized P&L on top of the account)
        eq_points = []
        running = self.account_size
        peak = running
        max_dd = 0.0
        for t in sorted(trades, key=lambda x: x.exit_date):
            running += t.dollar_pnl
            peak = max(peak, running)
            if peak > 0:
                max_dd = max(max_dd, (peak - running) / peak * 100.0)
            eq_points.append({"date": t.exit_date, "equity": round(running, 2)})
        total_pnl = sum(t.dollar_pnl for t in trades)

        best = max(trades, key=lambda t: t.r_multiple, default=None)
        worst = min(trades, key=lambda t: t.r_multiple, default=None)

        def _grp(key_fn, keys=None):
            out = {}
            ks = keys if keys is not None else sorted({key_fn(t) for t in trades})
            for k in ks:
                sub = [t for t in trades if key_fn(t) == k]
                w = [t for t in sub if t.win]
                out[k] = {
                    "count": len(sub),
                    "win_rate": round(len(w) / len(sub) * 100, 1) if sub else 0.0,
                    "avg_r": round(_mean([t.r_multiple for t in sub]), 3),
                    "best_r": round(max((t.r_multiple for t in sub), default=0.0), 2),
                }
            return out

        by_ticker = _grp(lambda t: t.ticker)
        top_tickers = sorted(
            ({"ticker": tk, "total_r": round(sum(t.r_multiple for t in trades if t.ticker == tk), 2),
              "trades": v["count"], "win_rate": v["win_rate"]} for tk, v in by_ticker.items()),
            key=lambda d: d["total_r"], reverse=True)

        exit_reasons = {}
        for reason in sorted({t.exit_reason for t in trades}):
            c = sum(1 for t in trades if t.exit_reason == reason)
            exit_reasons[reason] = {"count": c, "pct": round(c / n_tr * 100, 1) if n_tr else 0.0}
        n_scaled = sum(1 for t in trades if t.scaled)        # +10% scale-out trigger rate

        def _trade_brief(t):
            return None if t is None else {"ticker": t.ticker, "date": t.entry_date,
                                           "r": round(t.r_multiple, 2), "pattern": t.pattern}

        return {
            "params": {"start": self.start_date.strftime("%Y-%m-%d"),
                       "end": self.end_date.strftime("%Y-%m-%d"),
                       "account_size": self.account_size, "risk_pct": self.risk_pct,
                       "universe_size": len(self.universe),
                       "max_positions": self.max_positions},
            "total_signals": n_sig, "total_trades": n_tr,
            "fill_rate_pct": round(n_tr / n_sig * 100, 1) if n_sig else 0.0,
            "win_rate_pct": round(len(winners) / n_tr * 100, 1) if n_tr else 0.0,
            "total_winners": len(winners), "total_losers": len(losers),
            "avg_r_all": round(_mean(rs), 3),
            "avg_r_winners": round(_mean([t.r_multiple for t in winners]), 3),
            "avg_r_losers": round(_mean([t.r_multiple for t in losers]), 3),
            "median_r": round(float(np.median(rs)), 3) if rs else 0.0,
            "expectancy": round(_mean(rs), 3),
            "best_trade": _trade_brief(best), "worst_trade": _trade_brief(worst),
            "total_dollar_pnl": round(total_pnl, 2),
            "avg_dollar_per_trade": round(total_pnl / n_tr, 2) if n_tr else 0.0,
            "max_drawdown_pct": round(max_dd, 2),
            "final_equity": round(self.account_size + total_pnl, 2),
            "total_return_pct": round(total_pnl / self.account_size * 100, 2),
            "by_pattern": _grp(lambda t: t.pattern),
            "by_regime": _grp(lambda t: t.regime, keys=["BULL", "NEUTRAL", "BEAR"]),
            "top_tickers": top_tickers,
            "exit_reasons": exit_reasons,
            "scaled_count": n_scaled,
            "scaled_pct": round(n_scaled / n_tr * 100, 1) if n_tr else 0.0,
            "avg_days_held": round(_mean([t.days_held for t in trades]), 1),
            "avg_days_winners": round(_mean([t.days_held for t in winners]), 1),
            "avg_days_losers": round(_mean([t.days_held for t in losers]), 1),
            "daily_equity": eq_points,
        }

    # --------------------------------------------------------------- run/save
    def run(self) -> dict:
        log.info("Loading data for %d tickers...", len(self.universe))
        self.load_data()
        log.info("Scanning %d trading days...", len(self.trading_days))
        self.scan_all_dates()
        log.info("Simulating %d signals...", len(self.signals))
        self.simulate_trades()
        results = self.compute_results()
        self.save_results(results)
        self._print_summary(results)
        return results

    def _print_summary(self, r: dict) -> None:
        print(f"\n=== BACKTEST {r['params']['start']} -> {r['params']['end']} ===")
        print(f"Signals {r['total_signals']} | Trades {r['total_trades']} "
              f"(fill {r['fill_rate_pct']}%) | Win {r['win_rate_pct']}% | "
              f"Expectancy {r['expectancy']}R | Return {r['total_return_pct']}% | "
              f"MaxDD {r['max_drawdown_pct']}%")

    def _tag(self) -> str:
        return f"{self.start_date.strftime('%Y%m%d')}_{self.end_date.strftime('%Y%m%d')}"

    def save_results(self, results: dict) -> dict:
        out_dir = os.path.join(CONFIG.paths.data_dir, "backtest")
        os.makedirs(out_dir, exist_ok=True)
        tag = self._tag()
        paths = {}
        jpath = os.path.join(out_dir, f"results_{tag}.json")
        with open(jpath, "w", encoding="utf-8") as f:
            json.dump({**results, "generated_at": datetime.now().isoformat()}, f, indent=2)
        paths["results"] = jpath
        if self.trades:
            tpath = os.path.join(out_dir, f"trades_{tag}.csv")
            pd.DataFrame([asdict(t) for t in self.trades]).to_csv(tpath, index=False)
            paths["trades"] = tpath
        if self.signals:
            spath = os.path.join(out_dir, f"signals_{tag}.csv")
            pd.DataFrame([asdict(s) for s in self.signals]).to_csv(spath, index=False)
            paths["signals"] = spath
        log.info("Saved backtest artifacts: %s", ", ".join(paths.values()))
        return paths
