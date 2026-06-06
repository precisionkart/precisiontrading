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

# The 14 additive layers (3.9), in spec order. Prerequisites are tracked
# separately because they gate rather than add.
LAYER_NAMES: tuple[str, ...] = (
    "regime_bull",
    "tight_contraction",
    "valid_pattern",
    "stage_2",
    "support_resistance_flip",
    "correct_ma_reaction",
    "hot_theme",
    "timeframe_continuity",
    "top_industry_group",
    "strong_growth",
    "ipo_edge",
    "beach_ball",
    "volume_confirmation",
    "reward_risk",
    "earnings_flag",
)

# Human-readable labels for output.
LAYER_LABELS: dict[str, str] = {
    "regime_bull": "Regime bull / on buy signal",
    "tight_contraction": "Tight contraction (converged EMAs)",
    "valid_pattern": "Valid bullish pattern",
    "stage_2": "Stage 2 (or clean 1->2)",
    "support_resistance_flip": "Support->resistance flip",
    "correct_ma_reaction": "Correct MA reaction (riding 5/10/20)",
    "hot_theme": "Hot theme / leading sector",
    "timeframe_continuity": "Time-frame continuity",
    "top_industry_group": "Top 5-10 industry group",
    "strong_growth": "Strong + accelerating growth",
    "ipo_edge": "IPO price-discovery edge",
    "beach_ball": "Beach-ball relative strength",
    "volume_confirmation": "Volume confirmation (RVOL>2 / surge)",
    "reward_risk": "R:R >= 5:1",
    "earnings_flag": "Earnings flag breakout (highest-edge)",
}

# Prerequisites (3.9): if any is explicitly False the setup is disqualified.
PREREQUISITES: tuple[str, ...] = ("chart_ok", "not_earnings_gap_down")


@dataclass
class ScoreResult:
    score: float
    fired: list[str] = field(default_factory=list)       # layer keys that fired
    contributions: dict[str, float] = field(default_factory=dict)
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
    """Compute pinpoint_score from a dict of fired layer flags.

    `layers` may include the additive LAYER_NAMES (bool) and the PREREQUISITES
    (bool; default True if omitted — prerequisites only disqualify when an
    upstream module explicitly sets them False).
    """
    weights = weights or CONFIG.layers
    wd = weights.as_dict()

    # Prerequisite gate.
    disq_reasons: list[str] = []
    if layers.get("chart_ok", True) is False:
        disq_reasons.append("chart not clean (human-judgment prerequisite)")
    if layers.get("not_earnings_gap_down", True) is False:
        disq_reasons.append("gapped DOWN on earnings (reaction > numbers)")
    if disq_reasons:
        return ScoreResult(score=0.0, disqualified=True, disqualify_reasons=disq_reasons)

    fired: list[str] = []
    contributions: dict[str, float] = {}
    total = 0.0
    for name in LAYER_NAMES:
        if layers.get(name, False):
            w = wd.get(name, 0.0)
            fired.append(name)
            contributions[name] = w
            total += w

    return ScoreResult(score=round(total, 3), fired=fired, contributions=contributions)


def max_possible_score(weights: LayerWeights | None = None) -> float:
    """The ceiling if every additive layer fired — useful for normalising."""
    weights = weights or CONFIG.layers
    return round(sum(weights.as_dict().values()), 3)
