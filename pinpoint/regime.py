"""regime.py — market regime filter (spec Section 3.2).

Reads SPY and QQQ to decide whether the strategy is ON today. The Finviz quote
fundament gives SMA20/50/200 as % distance (positive = price above), which is
enough for the macro/intermediate/short-term stack. The 10-EMA-cross and 20-EMA
recapture refinements need OHLCV and are layered in via `from_ohlcv` (Phase 3);
the snapshot path works today off Finviz alone.

Regime output (3.2):
  bull    — above 200 + above 20 (+ 10>20)  -> full aggression
  neutral — above 200 but losing the 20 EMA -> only top-layer setups, emphasise RS
  bear    — below 200                        -> throttle longs, build beach-ball list
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import CONFIG
from .finviz_client import to_num

# 5-state regime (Phase 10 step 7). The middle three replace the old single
# "neutral"; NEUTRAL is kept as a legacy alias for the Finviz-unavailable
# fallback only.
BULL = "bull"
NEUTRAL_BULL = "neutral-bull"
NEUTRAL_BEAR = "neutral-bear"
BEAR = "bear"
VERY_BEAR = "very-bear"
NEUTRAL = "neutral"                   # legacy fallback alias

# Suggested portfolio exposure by state (a SUGGESTION, not a prescription —
# the user makes the actual sizing decision).
EXPOSURE: dict[str, tuple[float, str]] = {
    BULL: (1.00, "Full"),
    NEUTRAL_BULL: (0.66, "Two-Thirds"),
    NEUTRAL_BEAR: (0.33, "One-Third"),
    BEAR: (0.10, "Minimal"),
    VERY_BEAR: (0.00, "Cash"),
    NEUTRAL: (0.33, "One-Third"),
}
REGIME_EMOJI: dict[str, str] = {
    BULL: "🟢", NEUTRAL_BULL: "🟢", NEUTRAL_BEAR: "🟡",
    BEAR: "🔴", VERY_BEAR: "⚫", NEUTRAL: "🟡",
}


@dataclass
class BenchmarkRead:
    symbol: str
    above_sma200: bool | None
    above_sma50: bool | None
    above_sma20: bool | None
    sma20_pct: float
    sma50_pct: float
    sma200_pct: float
    ema10_above_ema20: bool | None = None     # 10>20 EMA cross (OHLCV path only)
    note: str = ""


@dataclass
class Regime:
    state: str                       # one of the 5 states (or legacy 'neutral')
    benchmarks: dict[str, BenchmarkRead] = field(default_factory=dict)
    rationale: list[str] = field(default_factory=list)

    @property
    def is_on(self) -> bool:
        """Is swing-long risk allowed? Bull/neutral states = on (throttled by
        exposure); bear / very-bear = effectively off for new longs."""
        return self.state in (BULL, NEUTRAL_BULL, NEUTRAL_BEAR, NEUTRAL)

    @property
    def exposure_pct(self) -> float:
        return EXPOSURE.get(self.state, (0.33, "One-Third"))[0]

    @property
    def exposure_label(self) -> str:
        return EXPOSURE.get(self.state, (0.33, "One-Third"))[1]

    def banner(self) -> str:
        emoji = REGIME_EMOJI.get(self.state, "")
        return f"{emoji} REGIME: {self.state.upper()} — " + "; ".join(self.rationale)


def _read_benchmark(symbol: str, fundament: dict) -> BenchmarkRead:
    """Turn a Finviz fundament dict into a structured benchmark read."""
    sma20 = to_num(fundament.get("SMA20"))
    sma50 = to_num(fundament.get("SMA50"))
    sma200 = to_num(fundament.get("SMA200"))

    def above(x: float) -> bool | None:
        if x != x:  # NaN
            return None
        return x > 0

    return BenchmarkRead(
        symbol=symbol,
        above_sma200=above(sma200),
        above_sma50=above(sma50),
        above_sma20=above(sma20),
        sma20_pct=sma20,
        sma50_pct=sma50,
        sma200_pct=sma200,
    )


def classify(reads: dict[str, BenchmarkRead]) -> Regime:
    """Combine benchmark reads into one of 5 regime states (Phase 10 step 7),
    using SMA200 (macro), SMA20 (≈ the 20 EMA, short-term trend) and — when
    OHLCV is available — the 10>20 EMA cross to split the bull/bear neutrals:

        BULL          >200 & >20 & 10>20            -> Full
        NEUTRAL-BULL  >200 & >20 but 10 crossing 20 -> Two-Thirds
        NEUTRAL-BEAR  >200 but <20                  -> One-Third
        BEAR          <200                          -> Minimal
        VERY-BEAR     <200 & <20 & 10<20            -> Cash

    Aggregates across SPY/QQQ conservatively (any benchmark losing a level
    pulls the whole read down).
    """
    rationale: list[str] = []
    if not reads:
        return Regime(state=NEUTRAL, rationale=["regime unknown — no benchmark data"])
    below_200 = [r.symbol for r in reads.values() if r.above_sma200 is False]
    below_20 = [r.symbol for r in reads.values() if r.above_sma20 is False]
    ema_known = any(r.ema10_above_ema20 is not None for r in reads.values())
    ema_bull = ema_known and all(r.ema10_above_ema20 for r in reads.values()
                                 if r.ema10_above_ema20 is not None)
    ema_bear_cross = any(r.ema10_above_ema20 is False for r in reads.values())

    if below_200:
        if below_20 and ema_bear_cross:
            state = VERY_BEAR
            rationale.append(f"{', '.join(below_200)} below 200 & 20 with 10<20 EMA — cash / capital preservation")
        else:
            state = BEAR
            rationale.append(f"{', '.join(below_200)} below 200 SMA (macro trend lost) — minimal exposure")
    elif below_20:
        state = NEUTRAL_BEAR
        rationale.append(f"above 200 but {', '.join(below_20)} lost the 20 — one-third, top-layer setups only")
    else:                                                   # above 200 and 20
        if not ema_known or ema_bull:
            state = BULL
            rationale.append("SPY/QQQ above 200 & 20 with 10>20 EMA — full aggression")
        else:
            state = NEUTRAL_BULL
            rationale.append("above 200 & 20 but 10 EMA crossing 20 — two-thirds, momentum cooling")

    return Regime(state=state, benchmarks=reads, rationale=rationale)


def _ema_cross_flag(symbol: str):
    """10>20 EMA on the benchmark's daily closes (True/False), or None if OHLCV
    is unavailable. Used to refine the 5-state classification."""
    try:
        from . import ohlcv as ohlcv_mod
        daily = ohlcv_mod.fetch_daily(symbol).df
        if daily is None or len(daily) < 25 or "Close" not in daily.columns:
            return None
        close = daily["Close"]
        ema10 = close.ewm(span=10, adjust=False).mean().iloc[-1]
        ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
        if ema10 != ema10 or ema20 != ema20:
            return None
        return bool(ema10 > ema20)
    except Exception:  # noqa: BLE001
        return None


def from_fundaments(fundaments: dict[str, dict]) -> Regime:
    """Build a Regime from {symbol: Finviz fundament dict} (snapshot path)."""
    reads = {sym: _read_benchmark(sym, fund) for sym, fund in fundaments.items()}
    return classify(reads)


def fetch_regime(client) -> Regime:
    """Live path: pull SPY & QQQ fundaments via the FinvizClient (Section 3.2).

    Degrades gracefully: a benchmark that fails to fetch is simply omitted; if
    none come back we return a neutral regime with a warning rationale.
    """
    fundaments: dict[str, dict] = {}
    for sym in CONFIG.regime.benchmarks:
        fund = client.fetch_quote_fundament(sym)
        if fund:
            fundaments[sym] = fund
    if not fundaments:
        return Regime(state=NEUTRAL, rationale=["regime unknown — Finviz unavailable; defaulting to neutral"])
    reads = {sym: _read_benchmark(sym, fund) for sym, fund in fundaments.items()}
    for sym, r in reads.items():                           # refine with the 10>20 EMA cross
        r.ema10_above_ema20 = _ema_cross_flag(sym)
    return classify(reads)
