"""entries.py — entry triggers, the .89 liquidity stop, and R:R (spec Section 3.7).

Every trade = a technical TRIGGER + a true STOP (where the thesis is invalidated)
+ a reward that is >= 5x the risk.

The .89 / liquidity rule: liquidity clusters at whole / half / quarter numbers,
so the stop is placed JUST BELOW the nearest cluster at or under the support low,
with a cluster-specific offset (config.EntryConfig):
    whole   (X.00)      -> -0.11   (45.00 -> 44.89)
    half    (X.50)      -> -0.01   (45.50 -> 45.49)
    quarter (X.25/X.75) -> -0.06   (45.25 -> 45.19 ; 45.75 -> 45.69)

R:R uses a measured target (supplied by the pattern's projected move when
available); the 3R and 5R target PRICES are always reported. A setup only
qualifies for Focus when R:R >= 5:1 (config.EntryConfig.min_reward_risk).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .config import CONFIG


@dataclass
class StopResult:
    stop: float
    cluster_level: float
    cluster_kind: str      # "whole" | "half" | "quarter"


def liquidity_stop(support_low: float, cfg=None) -> StopResult:
    """Place a .89-style stop just below the nearest liquidity cluster at or
    below `support_low` (3.7)."""
    cfg = cfg or CONFIG.entry
    if support_low is None or support_low != support_low or support_low <= 0:
        return StopResult(stop=float("nan"), cluster_level=float("nan"), cluster_kind="n/a")

    base = math.floor(support_low)
    eps = 1e-9
    # clusters within this whole-dollar band, classified.
    clusters = [(base + 0.00, "whole"),
                (base + 0.25, "quarter"),
                (base + 0.50, "half"),
                (base + 0.75, "quarter")]
    at_or_below = [(lvl, kind) for lvl, kind in clusters if lvl <= support_low + eps]
    if not at_or_below:                     # support_low below base (shouldn't happen)
        level, kind = base + 0.00, "whole"
    else:
        level, kind = max(at_or_below, key=lambda x: x[0])

    offset = {"whole": cfg.stop_offset_whole,
              "half": cfg.stop_offset_half,
              "quarter": cfg.stop_offset_quarter}[kind]
    stop = round(level - offset, 2)
    return StopResult(stop=stop, cluster_level=round(level, 2), cluster_kind=kind)


@dataclass
class EntrySetup:
    trigger: float                 # technical trigger (breakout / pivot high)
    entry: float                   # buy-stop = trigger + one tick
    stop: float
    stop_cluster: float
    stop_kind: str
    risk: float                    # per-share risk (entry - stop)
    target_3r: float
    target_5r: float
    measured_target: Optional[float]   # pattern's projected target, if any
    reward_risk: Optional[float]   # (measured_target - entry) / risk
    rr_ok: bool                    # R:R >= 5:1 (or measured target reaches 5R)
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "trigger": self.trigger, "entry": self.entry, "stop": self.stop,
            "stop_cluster": self.stop_cluster, "stop_kind": self.stop_kind,
            "risk": round(self.risk, 2) if self.risk == self.risk else self.risk,
            "target_3r": self.target_3r, "target_5r": self.target_5r,
            "measured_target": self.measured_target,
            "reward_risk": (round(self.reward_risk, 2)
                            if self.reward_risk is not None else None),
            "rr_ok": self.rr_ok, "note": self.note,
        }


def compute_setup(trigger: float, support_low: float,
                  measured_target: Optional[float] = None, cfg=None) -> EntrySetup:
    """Build a full entry setup from a trigger, a support low, and an optional
    measured (pattern) target.

    R:R is the measured target's distance over the risk when a target is given;
    otherwise it stays None and the setup does not qualify (we never invent a 5:1
    we can't justify).
    """
    cfg = cfg or CONFIG.entry
    sr = liquidity_stop(support_low, cfg)
    entry = round(trigger + cfg.buy_stop_tick, 2)
    risk = round(entry - sr.stop, 2)

    if risk <= 0 or risk != risk:
        return EntrySetup(trigger=trigger, entry=entry, stop=sr.stop,
                          stop_cluster=sr.cluster_level, stop_kind=sr.cluster_kind,
                          risk=risk, target_3r=float("nan"), target_5r=float("nan"),
                          measured_target=measured_target, reward_risk=None,
                          rr_ok=False, note="invalid risk (stop above entry)")

    target_3r = round(entry + 3.0 * risk, 2)
    target_5r = round(entry + 5.0 * risk, 2)

    reward_risk: Optional[float] = None
    note = ""
    rr_ok = False
    if measured_target is not None and measured_target == measured_target:
        reward_risk = (measured_target - entry) / risk
        rr_ok = reward_risk >= cfg.min_reward_risk
        if not rr_ok:
            note = f"measured target only {reward_risk:.1f}R (< {cfg.min_reward_risk:.0f}:1)"
    else:
        note = "no measured target — R:R unconfirmed"

    return EntrySetup(trigger=round(trigger, 2), entry=entry, stop=sr.stop,
                      stop_cluster=sr.cluster_level, stop_kind=sr.cluster_kind,
                      risk=risk, target_3r=target_3r, target_5r=target_5r,
                      measured_target=(round(measured_target, 2)
                                       if measured_target is not None else None),
                      reward_risk=reward_risk, rr_ok=rr_ok, note=note)
