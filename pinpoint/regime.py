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

BULL = "bull"
NEUTRAL = "neutral"
BEAR = "bear"


@dataclass
class BenchmarkRead:
    symbol: str
    above_sma200: bool | None
    above_sma50: bool | None
    above_sma20: bool | None
    sma20_pct: float
    sma50_pct: float
    sma200_pct: float
    note: str = ""


@dataclass
class Regime:
    state: str                       # bull / neutral / bear
    benchmarks: dict[str, BenchmarkRead] = field(default_factory=dict)
    rationale: list[str] = field(default_factory=list)

    @property
    def is_on(self) -> bool:
        """Is swing-long risk allowed? Bull or neutral = on (throttled);
        bear = effectively off for new longs."""
        return self.state in (BULL, NEUTRAL)

    def banner(self) -> str:
        emoji = {"bull": "🟢", "neutral": "🟡", "bear": "🔴"}.get(self.state, "")
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
    """Combine benchmark reads into a single regime.

    Decision: bear if ANY benchmark is below its 200 SMA (macro trend lost);
    else bull if ALL benchmarks are above their 20 EMA proxy (SMA20 here);
    else neutral (above 200 but losing the short-term trend).
    """
    rationale: list[str] = []
    below_200 = [r.symbol for r in reads.values() if r.above_sma200 is False]
    above_20_all = reads and all(r.above_sma20 for r in reads.values()
                                 if r.above_sma20 is not None)
    above_200_all = reads and all(r.above_sma200 for r in reads.values()
                                  if r.above_sma200 is not None)

    if below_200:
        state = BEAR
        rationale.append(f"{', '.join(below_200)} below 200 SMA (macro trend lost)")
        rationale.append("throttle longs; build the beach-ball watchlist for the turn")
    elif above_200_all and above_20_all:
        state = BULL
        rationale.append("SPY/QQQ above 200 & 20 SMA — full aggression")
    else:
        state = NEUTRAL
        rationale.append("above 200 SMA but losing the 20 — top-layer setups only, emphasise RS")

    return Regime(state=state, benchmarks=reads, rationale=rationale)


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
    return from_fundaments(fundaments)
