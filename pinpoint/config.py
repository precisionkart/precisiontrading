"""config.py — SINGLE source of truth for every threshold and Finviz screen.

Implements the codified parameters of the Pinpoint Trading strategy
(spec Sections 3 and 4). Nothing in the rest of the package should hard-code a
numeric threshold or a Finviz filter label; import it from here instead.

Finviz note (verified against finvizfinance==1.3.0): `set_filter` accepts the
HUMAN-READABLE option labels (e.g. "Over $10"), which are the dict values we
store below. The raw Finviz codes (e.g. ``sh_price_o10``) are kept alongside
for documentation/debugging only. All labels and codes here were verified to
exist in the installed package's ``constants.filter_dict``/``signal_dict``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _delay_default() -> float:
    """Finviz request delay, overridable via PINPOINT_REQUEST_DELAY (e.g. 2-3s
    from CI / cloud where 403s are more likely)."""
    try:
        return max(0.0, float(os.environ.get("PINPOINT_REQUEST_DELAY", "1")))
    except ValueError:
        return 1.0


# ---------------------------------------------------------------------------
# 3.3  UNIVERSE GATES — hard filters; every candidate must pass ALL of these.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UniverseGates:
    min_price: float = 10.0                 # Price > $10 (no penny names)
    min_avg_volume: int = 300_000           # Average volume >= 300k/day
    min_rel_volume: float = 2.0             # Relative Volume > 2.0
    min_rs_rating: int = 90                 # RS proxy 1-99 > 90 (see rs_rating.py)
    max_pct_below_high: float = 10.0        # within 0-10% of 52-week high
    top_group_pct: float = 10.0            # industry/group RS in top 10%

    # Preferred but NOT gates — add scoring weight only (3.3).
    preferred_beta: float = 2.0
    preferred_adr_pct: float = 5.0


# ---------------------------------------------------------------------------
# 3.4  FUNDAMENTAL QUALITY — growth = the catalyst engine.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FundamentalThresholds:
    min_eps_qoq_growth: float = 25.0        # current-quarter EPS YoY >= 25%
    min_annual_eps_growth: float = 25.0     # 3-5yr annual EPS >= 25%
    min_sales_growth: float = 25.0          # revenue growth >= 25% annualized
    triple_digit: float = 100.0             # EPS or sales >= 100% = elite signal
    # Acceleration (e.g. 50 -> 70 -> 80%) is the top fundamental indicator (3.4).


# ---------------------------------------------------------------------------
# 3.2  MARKET REGIME — read off SPY and QQQ.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RegimeConfig:
    benchmarks: tuple[str, ...] = ("SPY", "QQQ")
    sma_macro: int = 200                    # macro trend
    sma_intermediate: int = 50              # intermediate (least weighted)
    ema_signal: int = 20                    # short-term / the signal
    ema_fast: int = 10                      # 10 EMA cross above 20 confirms
    ema_momentum: int = 5                   # momentum MA


# ---------------------------------------------------------------------------
# 3.7  ENTRY / STOP / RISK-REWARD mechanics.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EntryConfig:
    min_reward_risk: float = 5.0            # R:R must be >= 5:1 to take a trade
    trim_levels: tuple[float, ...] = (3.0, 5.0)   # trim at 3:1 and 5:1
    # The .89 / liquidity rule (3.7): liquidity clusters at whole/half/quarter
    # numbers; place the stop JUST BELOW the nearest cluster at/under the support
    # low. Offset depends on which cluster it is (user-specified):
    #   whole   (X.00)      -> -0.11  (45.00 -> 44.89, ends in .89)
    #   half    (X.50)      -> -0.01  (45.50 -> 45.49)
    #   quarter (X.25/X.75) -> -0.06  (45.25 -> 45.19, 45.75 -> 45.69)
    stop_offset_whole: float = 0.11
    stop_offset_half: float = 0.01
    stop_offset_quarter: float = 0.06
    buy_stop_tick: float = 0.01            # buy-stop one tick above the trigger
    # The stop hugs the IMMEDIATE pivot (the tight coil low over the last N
    # bars), not the full pattern low — that is how the strategy gets a small
    # risk against a large measured move to reach R:R >= 5:1 (3.7). The strategy
    # front-runs with inside-day / pivot-low risk, so this is a short window.
    stop_lookback: int = 3


# ---------------------------------------------------------------------------
# 3.9  LAYERS OF PROBABILITY — the grading / ranking engine.
# Weights are additive; chart/pattern/contraction are PREREQUISITES handled in
# scoring.py (a bad chart or earnings gap-DOWN drops the name regardless).
# Time-frame continuity and beach-ball are weighted heaviest per the spec.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LayerWeights:
    """0-100 rescale (Phase 10 step 1). The additive base is 7 ShakeBot-mapped
    modules whose RAW weights sum to BASE_TOTAL (110) and are normalized to 100
    in scoring.py. Three bonuses are added AFTER normalization and the final
    score is capped at 100. Three legacy layers were migrated OUT of the score:
      regime_bull   -> 5-state regime / position-sizing suggestion (step 7)
      strong_growth -> "Triple-Digit Growth" plain-English flag (step 5)
      ipo_edge      -> IPO-page tagging only (not scored on the Focus list)
    """
    # --- Compression (25) ---
    tight_contraction: float = 25.0
    # --- Trend / Structure (25) ---
    stage_2: float = 10.0
    valid_pattern: float = 10.0
    support_resistance_flip: float = 5.0
    # --- MA Slopes (18) ---
    timeframe_continuity: float = 10.0
    correct_ma_reaction: float = 8.0
    # --- Breakout Ready (12) ---
    volume_confirmation: float = 12.0
    # --- Relative Strength (10) ---
    beach_ball: float = 10.0
    # --- Risk Quality (5) ---
    reward_risk: float = 5.0
    # --- Sector Strength (15) ---
    hot_theme: float = 8.0
    top_industry_group: float = 7.0

    # Bonuses — added after the base is normalized to 100, then capped at 100.
    volume_dry_up: float = 2.0             # 5-bar dry-up (step 5 flag)
    slingshot: float = 3.0                 # leader shakeout-and-reclaim (step 4)
    earnings_flag: float = 25.0            # 3.6 ⭐ tracked gap-up breaking out

    # Migrated out of the additive score (kept as 0.0 for back-compat refs).
    regime_bull: float = 0.0
    strong_growth: float = 0.0
    ipo_edge: float = 0.0

    def as_dict(self) -> dict[str, float]:
        """The normalized BASE only (the 7 modules; raw weights sum to 110)."""
        return {
            "tight_contraction": self.tight_contraction,
            "stage_2": self.stage_2,
            "valid_pattern": self.valid_pattern,
            "support_resistance_flip": self.support_resistance_flip,
            "timeframe_continuity": self.timeframe_continuity,
            "correct_ma_reaction": self.correct_ma_reaction,
            "volume_confirmation": self.volume_confirmation,
            "beach_ball": self.beach_ball,
            "reward_risk": self.reward_risk,
            "hot_theme": self.hot_theme,
            "top_industry_group": self.top_industry_group,
        }

    def bonus_dict(self) -> dict[str, float]:
        """Bonuses added after normalization (score then capped at 100)."""
        return {
            "volume_dry_up": self.volume_dry_up,
            "slingshot": self.slingshot,
            "earnings_flag": self.earnings_flag,
        }


# ---------------------------------------------------------------------------
# 3.10  THEMES & SECTOR ROTATION — ETFs ranked by RS vs SPY.
# ---------------------------------------------------------------------------
# Each theme is its own ranked group so a stock's sector/industry maps to a
# specific theme rank. Specialized growth themes (Semis/Uranium/AI/Solar) plus
# the broad SPDR sectors as individual themes.
THEME_ETFS: dict[str, list[str]] = {
    "Semiconductors": ["SMH", "SOXX"],
    "Uranium/Nuclear": ["URNM", "NLR", "URA"],
    "AI/Robotics": ["AIQ", "BOTZ", "ROBO"],
    "Solar": ["TAN"],
    "Technology": ["XLK"],
    "Energy": ["XLE"],
    "Financials": ["XLF"],
    "Consumer Cyclical": ["XLY"],
    "Healthcare": ["XLV"],
    "Industrials": ["XLI"],
}
THEME_BENCHMARK = "SPY"

# Thresholds for the theme / industry scoring layers (3.10).
HOT_THEME_TOP_FRAC = 0.30          # stock's theme in top 30% of themes -> hot
TOP_INDUSTRY_FRAC = 0.10          # stock's industry in top 10% of industries


# ---------------------------------------------------------------------------
# 3.11  IPO sub-strategy.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IpoConfig:
    max_age_label: str = "In the last year"   # Finviz IPO Date label
    min_price: float = 10.0
    min_avg_volume: int = 300_000


# ---------------------------------------------------------------------------
# SECTION 4 — VERIFIED FINVIZ REFERENCE.
# `view_id` numbers and the filter LABELS below were all confirmed against
# finvizfinance==1.3.0. Pass labels to ScreenerView.set_filter(filters_dict=).
# ---------------------------------------------------------------------------
FINVIZ_VIEWS: dict[str, int] = {
    "overview": 111,     # Sector, Industry, Company
    "valuation": 121,    # EPS this Y, EPS past 5Y, Sales past 5Y
    "financial": 161,
    "performance": 141,  # Perf Week/Month/Quart/Half/Year (RS proxy), Avg/Rel Vol
    "technical": 171,    # Beta, ATR, SMA20/50/200 %dist, 52W High/Low, RSI, Gap
}

# label -> raw Finviz code (documentation; finvizfinance maps label internally).
FINVIZ_FILTER_CODES: dict[str, str] = {
    "Price:Over $10": "sh_price_o10",
    "Average Volume:Over 300K": "sh_avgvol_o300",
    "Relative Volume:Over 2": "sh_relvol_o2",
    "52-Week High/Low:0-10% below High": "ta_highlow52w_b0to10h",
    "52-Week High/Low:New High": "ta_highlow52w_nh",
    "EPS growthqtr over qtr:Over 25%": "fa_epsqoq_o25",
    "Sales growthqtr over qtr:Over 25%": "fa_salesqoq_o25",
    "200-Day Simple Moving Average:Price above SMA200": "ta_sma200_pa",
    "50-Day Simple Moving Average:SMA50 above SMA200": "ta_sma50_sa200",
    "50-Day Simple Moving Average:Price above SMA50": "ta_sma50_pa",
    "Earnings Date:Yesterday After Market Close": "fa_earningsdate_yesterdayafter",
    "Earnings Date:Today Before Market Open": "fa_earningsdate_todaybefore",
    "Beta:Over 1.5": "ta_beta_o1.5",
    "Beta:Over 2": "ta_beta_o2",
    "IPO Date:In the last year": "ipodate_prevyear",
}


# The screens themselves, as {Finviz filter name: option label} dicts ready to
# hand to ScreenerView.set_filter(filters_dict=...). (3.3 + Section 4.)
TARGETS_SCREEN: dict[str, str] = {
    "Price": "Over $10",
    "Average Volume": "Over 300K",
    "Relative Volume": "Over 2",
    "52-Week High/Low": "0-10% below High",
    "200-Day Simple Moving Average": "Price above SMA200",
    "50-Day Simple Moving Average": "SMA50 above SMA200",
}

# Broad universe for the My-Picks RS reference (Section 7B fidelity note): NOT
# RVOL-gated, so RS is a meaningful percentile across ~hundreds of names rather
# than the few dozen RVOL>2 movers in TARGETS_SCREEN. Pulled with the performance
# view only (RS just needs trailing returns).
RS_REFERENCE_SCREEN: dict[str, str] = {
    "Price": "Over $10",
    "Average Volume": "Over 300K",
    "52-Week High/Low": "0-10% below High",
}

# Optional hard fundamental-growth filters (3.4), applied to the Targets screen
# ONLY when the user passes --min-growth. By default growth is a weighted scoring
# layer, not a hard gate (honors "chart first" + the early-stage/explosive-sales
# carve-out in 3.4). This lets us compare both modes on real data.
GROWTH_FILTERS: dict[str, str] = {
    "EPS growthqtr over qtr": "Over 25%",
    "Sales growthqtr over qtr": "Over 25%",
}

# Earnings screen (3.3 / output 7): reported yesterday-after-close or
# today-before-open, price > $10, avg vol >= 300k. The gap-UP-only rule is
# applied in code (pipeline) because Finviz can't express "gapped up only"
# precisely — reaction > numbers (exclude any gap-down regardless).
EARNINGS_SCREEN_YESTERDAY: dict[str, str] = {
    "Price": "Over $10",
    "Average Volume": "Over 300K",
    "Earnings Date": "Yesterday After Market Close",
}
EARNINGS_SCREEN_TODAY: dict[str, str] = {
    "Price": "Over $10",
    "Average Volume": "Over 300K",
    "Earnings Date": "Today Before Market Open",
}

# Native Finviz pattern signals (set_filter(signal=...)) — cross-checked in
# patterns.py against geometric detection (3.6 / Section 4).
FINVIZ_SIGNALS: dict[str, str] = {
    "Head & Shoulders Inverse": "ta_p_headandshouldersinv",
    "Channel Up": "ta_p_channelup",
    "Channel Down": "ta_p_channeldown",     # inside an uptrend = bullish desc. channel
    "Wedge Up": "ta_p_wedgeup",
    "Wedge Down": "ta_p_wedgedown",
    "Wedge": "ta_p_wedge",
    "Triangle Ascending": "ta_p_wedgeresistance",
    "Triangle Descending": "ta_p_wedgesupport",
    "Horizontal S/R": "ta_p_horizontal",
    "Channel": "ta_p_channel",
    "TL Support": "ta_p_tlsupport",
    "TL Resistance": "ta_p_tlresistance",
    "Double Bottom": "ta_p_doublebottom",
    "Multiple Bottom": "ta_p_multiplebottom",
}

# Sorting (Section 4): order='Performance (Quarter)', ascend=False -> -perf13w.
FINVIZ_SORT_QUARTER = "Performance (Quarter)"


# ---------------------------------------------------------------------------
# Networking / anti-scraping (Section 4) and paths.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class NetworkConfig:
    request_delay_s: float = field(default_factory=_delay_default)  # >=1s between pages
    max_retries: int = 3
    backoff_base_s: float = 2.0
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )


@dataclass(frozen=True)
class Paths:
    data_dir: str = "data"                 # OHLCV cache (gitignored)
    output_dir: str = "output"             # CSV/XLSX/HTML reports
    store_dir: str = "data/store"          # last-scan snapshot + watchlist


# ---------------------------------------------------------------------------
# Output sizing (Section 7).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OutputSizes:
    targets_min: int = 100
    targets_max: int = 200
    focus_min: int = 10
    focus_max: int = 15


# ---------------------------------------------------------------------------
# Aggregate config object — import `CONFIG` everywhere.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Config:
    gates: UniverseGates = field(default_factory=UniverseGates)
    fundamentals: FundamentalThresholds = field(default_factory=FundamentalThresholds)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    entry: EntryConfig = field(default_factory=EntryConfig)
    layers: LayerWeights = field(default_factory=LayerWeights)
    ipo: IpoConfig = field(default_factory=IpoConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    paths: Paths = field(default_factory=Paths)
    output: OutputSizes = field(default_factory=OutputSizes)


CONFIG = Config()
