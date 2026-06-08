"""scoring.py — the pinpoint_score grading engine (spec Section 3.9).

A setup's grade = how many independent edges stack ("layers of probability").
Chart / contraction / pattern are PREREQUISITES: a bad chart, or an earnings
gap-DOWN, disqualifies the name regardless of fundamentals. The remaining layers
are additive weight (config.LayerWeights), with time-frame continuity and the
beach-ball signal weighted heaviest.

`score_layers` returns a `ScoreResult` carrying the weighted sum plus an itemised
breakdown of exactly which layers fired and what each contributed — so every
ranking is transparent (Section 3.13 codifiability map).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import CONFIG, LayerWeights

# --- 0-100 rescale (Phase 10 step 1) -------------------------------------
# The additive BASE is 7 ShakeBot-mapped modules whose RAW weights sum to
# BASE_TOTAL; the base is normalized to SCORE_MAX. BONUS layers are added AFTER
# normalization and the final score is capped at SCORE_MAX.
BASE_TOTAL: float = 110.0
SCORE_MAX: float = 100.0
_NORMALIZE: float = SCORE_MAX / BASE_TOTAL

# The 11 additive base layers (the 7 modules), in module order.
LAYER_NAMES: tuple[str, ...] = (
    "tight_contraction",        # Compression (25)
    "stage_2",                  # Trend/Structure (25)
    "valid_pattern",
    "support_resistance_flip",
    "timeframe_continuity",     # MA Slopes (18)
    "correct_ma_reaction",
    "volume_confirmation",      # Breakout Ready (12)
    "beach_ball",               # Relative Strength (10)
    "reward_risk",              # Risk Quality (5)
    "hot_theme",                # Sector Strength (15)
    "top_industry_group",
)

# Bonus layers — added after normalization, score then capped at 100.
BONUS_NAMES: tuple[str, ...] = ("volume_dry_up", "slingshot", "earnings_flag")

# Tier thresholds on the 0-100 scale (Phase 10 step 1 / step 6).
TIER_ELITE: float = 80.0
TIER_GOOD: float = 65.0
TIER_WATCH: float = 50.0


def tier(score: float) -> str | None:
    """Map a 0-100 score to its visual tier (None below the watchlist floor)."""
    if score >= TIER_ELITE:
        return "Elite"
    if score >= TIER_GOOD:
        return "Good"
    if score >= TIER_WATCH:
        return "Watchlist"
    return None


# Legacy (pre-rescale) weights — kept so each row can carry a `score_legacy`
# for one release to compare relative ranking before/after the rescale.
_LEGACY_WEIGHTS: dict[str, float] = {
    "regime_bull": 1.0, "tight_contraction": 1.0, "valid_pattern": 1.5,
    "stage_2": 1.5, "support_resistance_flip": 1.0, "correct_ma_reaction": 1.0,
    "hot_theme": 1.0, "timeframe_continuity": 2.0, "top_industry_group": 1.0,
    "strong_growth": 1.5, "ipo_edge": 0.5, "beach_ball": 2.0,
    "volume_confirmation": 1.0, "reward_risk": 1.0, "earnings_flag": 4.0,
}


def _legacy_score(layers: dict[str, bool]) -> float:
    return round(sum(w for k, w in _LEGACY_WEIGHTS.items() if layers.get(k)), 3)


# Human-readable labels for output.
LAYER_LABELS: dict[str, str] = {
    "tight_contraction": "Tight contraction (converged EMAs)",
    "valid_pattern": "Valid bullish pattern",
    "stage_2": "Stage 2 (or clean 1->2)",
    "support_resistance_flip": "Support->resistance flip",
    "correct_ma_reaction": "Correct MA reaction (riding 5/10/20)",
    "hot_theme": "Hot theme / leading sector",
    "timeframe_continuity": "Time-frame continuity",
    "top_industry_group": "Top 5-10 industry group",
    "beach_ball": "Beach-ball relative strength",
    "volume_confirmation": "Volume confirmation (RVOL>2 / surge)",
    "reward_risk": "R:R >= 5:1",
    "volume_dry_up": "Volume dry-up (+2 bonus)",
    "slingshot": "Slingshot reclaim (+3 bonus)",
    "earnings_flag": "Earnings flag breakout (+25 bonus, highest-edge)",
}

# Prerequisites (3.9): if any is explicitly False the setup is disqualified.
PREREQUISITES: tuple[str, ...] = ("chart_ok", "not_earnings_gap_down")


@dataclass
class ScoreResult:
    score: float                                          # 0-100 (capped)
    fired: list[str] = field(default_factory=list)       # layer keys that fired
    contributions: dict[str, float] = field(default_factory=dict)  # normalized pts
    score_legacy: float = 0.0                            # pre-rescale 0-~13 score
    tier: str | None = None                              # Elite / Good / Watchlist
    disqualified: bool = False
    disqualify_reasons: list[str] = field(default_factory=list)

    @property
    def fired_labels(self) -> list[str]:
        return [LAYER_LABELS.get(k, k) for k in self.fired]

    def breakdown_str(self) -> str:
        if self.disqualified:
            return "DISQUALIFIED: " + "; ".join(self.disqualify_reasons)
        parts = [f"{LAYER_LABELS.get(k, k)} (+{self.contributions[k]:.1f})" for k in self.fired]
        return " | ".join(parts) if parts else "(no layers fired)"


def score_layers(layers: dict[str, bool],
                 weights: LayerWeights | None = None) -> ScoreResult:
    """Compute the 0-100 pinpoint_score from a dict of fired layer flags.

    The base (LAYER_NAMES) raw weights sum to BASE_TOTAL and are normalized to
    SCORE_MAX; BONUS_NAMES are added afterwards and the result is capped at
    SCORE_MAX. `layers` may include the base/bonus flags and the PREREQUISITES
    (bool; default True — prerequisites only disqualify when an upstream module
    explicitly sets them False).
    """
    weights = weights or CONFIG.layers
    wd = weights.as_dict()
    bd = weights.bonus_dict()

    # Prerequisite gate.
    disq_reasons: list[str] = []
    if layers.get("chart_ok", True) is False:
        disq_reasons.append("chart not clean (human-judgment prerequisite)")
    if layers.get("not_earnings_gap_down", True) is False:
        disq_reasons.append("gapped DOWN on earnings (reaction > numbers)")
    if disq_reasons:
        return ScoreResult(score=0.0, score_legacy=0.0, disqualified=True,
                           disqualify_reasons=disq_reasons)

    fired: list[str] = []
    contributions: dict[str, float] = {}
    base_raw = 0.0
    for name in LAYER_NAMES:
        if layers.get(name, False):
            w = wd.get(name, 0.0)
            fired.append(name)
            contributions[name] = round(w * _NORMALIZE, 2)     # normalized points
            base_raw += w
    total = base_raw * _NORMALIZE

    for name in BONUS_NAMES:
        if layers.get(name, False):
            w = bd.get(name, 0.0)
            fired.append(name)
            contributions[name] = w
            total += w

    score = round(min(SCORE_MAX, total), 1)
    return ScoreResult(score=score, fired=fired, contributions=contributions,
                       score_legacy=_legacy_score(layers), tier=tier(score))


def max_possible_score(weights: LayerWeights | None = None) -> float:
    """The score ceiling after the cap — 100 on the rescaled engine."""
    return SCORE_MAX
