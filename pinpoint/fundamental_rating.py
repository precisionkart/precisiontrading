"""fundamental_rating.py — a PURE, isolated ShakeBot-style fundamental rating.

Step 2 of the fundamental-rating work. This module is intentionally NOT wired
into scoring, the Focus gate, D3, or Telegram — it is a self-contained function
plus its constants, designed to be tuned in isolation and wired up later.

Inputs are the fundamental fields we already have (any may be NaN/None):
  - growth:  eps_qoq, eps_this_y, eps_past5y, sales_qoq, sales_past5y
  - quality: net_margin, roe  (TTM, from the Step-1 get_financials extension)
  - demand:  accumulation, up_down_volume_ratio  (PRECOMPUTED from cached OHLCV
             by the caller — this function fetches nothing and stays pure)

Design notes:
  * All weights / thresholds / bands are module-level CONSTANTS (below) so they
    are trivially tunable.
  * NaN/None is NEVER treated as 0. A missing metric is EXCLUDED from its
    component and the component's weight is renormalized across the metrics that
    ARE present. A missing component is excluded from the overall and the
    component weights renormalized. `missing_inputs` records what was absent so
    the display can say "based on N of M metrics".
  * net_margin contributions are clamped at MARGIN_CAP; above that the raw number
    is treated as a likely one-time item (one_time_item_warning) and the CAP
    value is used for scoring, not the inflated figure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

NAN = float("nan")

# --- component weights (overall) -------------------------------------------
W_COMP_EPS = 0.45          # earnings growth is the primary driver
W_COMP_SMR = 0.35          # Sales + Margin + ROE (CANSLIM "S/M/R")
W_COMP_ACCUM = 0.20        # institutional demand proxy

# --- within-component metric weights ---------------------------------------
W_EPS_QOQ = 0.50
W_EPS_THIS_Y = 0.30
W_EPS_PAST5Y = 0.20

W_SALES_QOQ = 0.30
W_SALES_PAST5Y = 0.20
W_NET_MARGIN = 0.30
W_ROE = 0.20

W_ACCUM = 0.60
W_UDVR = 0.40

# --- per-metric scoring scale: value at/below LO -> 0, at/above HI -> 100 ---
EPS_QOQ_HI = 50.0          # %
EPS_THIS_Y_HI = 40.0
EPS_PAST5Y_HI = 30.0
SALES_QOQ_HI = 30.0
SALES_PAST5Y_HI = 25.0
MARGIN_HI = 25.0           # net margin % that earns a full mark
ROE_HI = 30.0
GROWTH_LO = 0.0            # growth at/below 0% scores 0
MARGIN_LO = 0.0
ROE_LO = 0.0
ACCUM_LO, ACCUM_HI = -1.0, 1.0     # caller passes a normalized A/D signal in [-1, 1]
UDVR_LO, UDVR_HI = 0.8, 2.0        # up/down volume ratio

# --- flags / clamps --------------------------------------------------------
MARGIN_CAP = 50.0          # SCORING clamp: margin contribution capped here so a
#                            spike can't inflate the score (independent of the flag)
STRONG_MARGIN = 20.0       # capped margin >= this -> strong_margin flag

# --- relative one-time-item detection (TUNE vs ShakeBot) -------------------
# The warning is RELATIVE: compare the latest single-quarter margin to the MEDIAN
# of the company's own older quarters (robust to outliers). Absolute MARGIN_CAP is
# only the scoring clamp above, NOT the flag.
ONE_TIME_SPIKE_K = 1.5     # latest >= K * baseline-median -> spike   (placeholder)
ONE_TIME_MIN_ABS = 40.0    # latest must exceed this to even consider a spike
ONE_TIME_MIN_HISTORY = 3   # need >= this many usable baseline quarters, else DON'T flag
TRIPLE_DIGIT = 100.0       # any growth >= this -> triple_digit_growth
MONSTER_GROWTH = 50.0      # any growth >= this -> monster_growth
ACCUM_POSITIVE = 0.0       # accumulation strictly above this -> positive
UDVR_POSITIVE = 1.0        # up/down ratio strictly above this -> positive

# --- letter-grade bands ----------------------------------------------------
GRADE_A_MIN = 80.0
GRADE_B_MIN = 65.0
GRADE_C_MIN = 50.0         # below GRADE_C_MIN -> "D"
GRADE_NA = "N/A"           # no data at all

# All input field names, in display order (drives missing_inputs / N-of-M).
INPUT_NAMES = ("eps_qoq", "eps_this_y", "eps_past5y", "sales_qoq", "sales_past5y",
               "net_margin", "roe", "accumulation", "up_down_volume_ratio")


@dataclass
class FundamentalRating:
    overall_score: float                       # 0-100, NaN if no data at all
    grade: str
    eps_score: Optional[float]                 # None if the whole component is absent
    smr_score: Optional[float]
    accum_score: Optional[float]
    flags: dict = field(default_factory=dict)
    missing_inputs: list = field(default_factory=list)
    n_present: int = 0
    n_total: int = len(INPUT_NAMES)
    summary: str = ""

    def as_dict(self) -> dict:
        return {
            "overall_score": self.overall_score, "grade": self.grade,
            "eps_score": self.eps_score, "smr_score": self.smr_score,
            "accum_score": self.accum_score, "flags": dict(self.flags),
            "missing_inputs": list(self.missing_inputs),
            "n_present": self.n_present, "n_total": self.n_total,
            "summary": self.summary,
        }


def _present(x) -> bool:
    return x is not None and x == x          # not None and not NaN


def _lin(v: float, lo: float, hi: float) -> float:
    """Linear map v in [lo, hi] -> [0, 100], clamped."""
    if hi == lo:
        return 0.0
    return max(0.0, min(100.0, (v - lo) / (hi - lo) * 100.0))


def _median(xs: list[float]) -> float:
    """Median of a non-empty list (robust to outliers). NaN on empty."""
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return NAN
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _component(parts: list[tuple[float, float]]) -> Optional[float]:
    """parts = [(weight, subscore), ...] for PRESENT metrics only.
    Returns the weight-renormalized average, or None if nothing is present."""
    if not parts:
        return None
    tot = sum(w for w, _ in parts)
    if tot <= 0:
        return None
    return sum(w * s for w, s in parts) / tot


def _grade(score: float) -> str:
    if score != score:
        return GRADE_NA
    if score >= GRADE_A_MIN:
        return "A"
    if score >= GRADE_B_MIN:
        return "B"
    if score >= GRADE_C_MIN:
        return "C"
    return "D"


def rate_fundamentals(eps_qoq=NAN, eps_this_y=NAN, eps_past5y=NAN,
                      sales_qoq=NAN, sales_past5y=NAN,
                      net_margin=NAN, roe=NAN,
                      accumulation=None, up_down_volume_ratio=None,
                      margin_history=None) -> FundamentalRating:
    """Pure fundamental rating. All inputs optional (NaN/None = missing).

    margin_history: per-quarter single-quarter net margins (%), newest-first
    (from get_financials). Used only for the RELATIVE one-time-item flag; absent
    or too-short history simply doesn't flag (and is noted in missing_inputs)."""
    margin_history = list(margin_history) if margin_history else []
    vals = dict(eps_qoq=eps_qoq, eps_this_y=eps_this_y, eps_past5y=eps_past5y,
                sales_qoq=sales_qoq, sales_past5y=sales_past5y,
                net_margin=net_margin, roe=roe, accumulation=accumulation,
                up_down_volume_ratio=up_down_volume_ratio)
    missing = [k for k in INPUT_NAMES if not _present(vals[k])]
    n_present = len(INPUT_NAMES) - len(missing)

    # ---- EPS component ----
    eps_parts = []
    if _present(eps_qoq):
        eps_parts.append((W_EPS_QOQ, _lin(eps_qoq, GROWTH_LO, EPS_QOQ_HI)))
    if _present(eps_this_y):
        eps_parts.append((W_EPS_THIS_Y, _lin(eps_this_y, GROWTH_LO, EPS_THIS_Y_HI)))
    if _present(eps_past5y):
        eps_parts.append((W_EPS_PAST5Y, _lin(eps_past5y, GROWTH_LO, EPS_PAST5Y_HI)))
    eps_score = _component(eps_parts)

    # ---- SMR component (sales + margin + roe) ----
    # Scoring clamp (independent of the one-time FLAG below): a margin spike can
    # never inflate the score beyond MARGIN_CAP.
    nm_capped = min(net_margin, MARGIN_CAP) if _present(net_margin) else NAN
    smr_parts = []
    if _present(sales_qoq):
        smr_parts.append((W_SALES_QOQ, _lin(sales_qoq, GROWTH_LO, SALES_QOQ_HI)))
    if _present(sales_past5y):
        smr_parts.append((W_SALES_PAST5Y, _lin(sales_past5y, GROWTH_LO, SALES_PAST5Y_HI)))
    if _present(net_margin):
        smr_parts.append((W_NET_MARGIN, _lin(nm_capped, MARGIN_LO, MARGIN_HI)))
    if _present(roe):
        smr_parts.append((W_ROE, _lin(roe, ROE_LO, ROE_HI)))
    smr_score = _component(smr_parts)

    # ---- Accumulation component ----
    accum_parts = []
    if _present(accumulation):
        accum_parts.append((W_ACCUM, _lin(accumulation, ACCUM_LO, ACCUM_HI)))
    if _present(up_down_volume_ratio):
        accum_parts.append((W_UDVR, _lin(up_down_volume_ratio, UDVR_LO, UDVR_HI)))
    accum_score = _component(accum_parts)

    # ---- overall (renormalize component weights over present components) ----
    comps = [(W_COMP_EPS, eps_score), (W_COMP_SMR, smr_score), (W_COMP_ACCUM, accum_score)]
    comp_present = [(w, s) for w, s in comps if s is not None]
    overall = _component(comp_present) if comp_present else NAN
    grade = _grade(overall)

    # ---- relative one-time-item detection (vs the company's own trailing norm) ----
    # baseline = MEDIAN of OLDER quarters (exclude the most recent 1 to avoid
    # recent-contamination), NaNs dropped. Flag only with enough usable history.
    baseline_q = [m for m in margin_history[1:] if _present(m)]
    latest_margin = margin_history[0] if (margin_history and _present(margin_history[0])) else NAN
    baseline_med = NAN
    one_time = False
    if len(baseline_q) >= ONE_TIME_MIN_HISTORY and _present(latest_margin):
        baseline_med = _median(baseline_q)
        if baseline_med == baseline_med and baseline_med > 0:
            one_time = bool(latest_margin >= ONE_TIME_SPIKE_K * baseline_med
                            and latest_margin > ONE_TIME_MIN_ABS)
    else:
        missing.append("margin_history (insufficient to judge one-time items)")

    # ---- flags ----
    growths = [eps_qoq, eps_this_y, eps_past5y, sales_qoq, sales_past5y]
    present_growths = [g for g in growths if _present(g)]
    flags = {
        "triple_digit_growth": any(g >= TRIPLE_DIGIT for g in present_growths),
        "monster_growth": any(g >= MONSTER_GROWTH for g in present_growths),
        "strong_margin": bool(_present(net_margin) and nm_capped >= STRONG_MARGIN),
        "one_time_item_warning": bool(one_time),
        "accumulation_positive": bool((_present(accumulation) and accumulation > ACCUM_POSITIVE)
                                      or (_present(up_down_volume_ratio)
                                          and up_down_volume_ratio > UDVR_POSITIVE)),
    }

    # ---- summary ----
    def _s(x):
        return f"{x:.0f}" if (x is not None and x == x) else "—"
    note = []
    if flags["triple_digit_growth"]:
        note.append("triple-digit growth")
    elif flags["monster_growth"]:
        note.append("monster growth")
    if flags["strong_margin"]:
        note.append("strong margin")
    if flags["one_time_item_warning"]:
        note.append("⚠ one-time item")
    if flags["accumulation_positive"]:
        note.append("accumulation")
    head = (f"{grade} ({overall:.0f}/100)" if overall == overall else f"{GRADE_NA} (no data)")
    summary = (f"{head} · EPS {_s(eps_score)}/SMR {_s(smr_score)}/Accum {_s(accum_score)} · "
               f"{n_present}/{len(INPUT_NAMES)} metrics"
               + (" · " + ", ".join(note) if note else ""))

    return FundamentalRating(
        overall_score=overall, grade=grade, eps_score=eps_score, smr_score=smr_score,
        accum_score=accum_score, flags=flags, missing_inputs=missing,
        n_present=n_present, n_total=len(INPUT_NAMES), summary=summary)
