"""fundamentals.py — growth quality, the catalyst engine (spec Section 3.4).

Growth is the most-weighted fundamental driver. Thresholds (config.Fundamentals):
  * current-quarter EPS growth (YoY) >= 25%
  * annual EPS growth, 3-5yr           >= 25%, consistent
  * revenue (sales) growth             >= 25% annualized
  * triple-digit growth (EPS or sales >= 100%) = elite signal
  * accelerating growth (e.g. 50->70->80%)     = top fundamental indicator
  * raised forward guidance                    = most important (human-judgment)

Honest-proxy notes (Section 1 guardrail):
  * Finviz's snapshot exposes magnitude (EPS this Y, EPS past 5Y, Sales past 5Y,
    and EPS/Sales qtr-over-qtr) but NOT a clean multi-quarter series, so
    quarter-by-quarter ACCELERATION cannot be measured exactly from the snapshot.
    We flag acceleration only when we can (e.g. current quarter EPS QoQ
    meaningfully exceeds the 5-yr annual rate) and label it a proxy.
  * Raised guidance is a human-judgment item — we never infer it silently; it is
    surfaced as a flag for the trader to confirm.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import CONFIG


@dataclass
class GrowthProfile:
    eps_qoq: float = float("nan")        # current-quarter EPS growth YoY (%)
    eps_annual_5y: float = float("nan")  # annual EPS growth, ~5yr (%)
    sales_qoq: float = float("nan")      # current-quarter sales growth (%)
    sales_5y: float = float("nan")       # sales growth, ~5yr (%)
    eps_this_y: float = float("nan")     # EPS growth this year (%)

    # derived flags
    strong_growth: bool = False          # passes any of the >=25% magnitude gates
    meets_eps_qoq: bool = False
    meets_annual_eps: bool = False
    meets_sales: bool = False
    triple_digit: bool = False           # EPS or sales >= 100%
    accelerating_proxy: bool = False     # labeled proxy (see module docstring)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Show the strongest AVAILABLE EPS and sales metric, honestly labeled.

        Current-quarter EPS/Sales Q/Q (the most-weighted 3.4 driver) is not a
        screener column — it is enriched per-ticker from the quote endpoint for
        the small Focus set (Phase 3). Until present, eps_qoq/sales_qoq are NaN
        and we fall back to the annual figures rather than printing 'nan%'.
        """
        bits: list[str] = []
        eps = [("EPS Q/Q", self.eps_qoq), ("EPS yr", self.eps_this_y),
               ("EPS 5y", self.eps_annual_5y)]
        eps = [(lbl, v) for lbl, v in eps if v == v]
        if eps:
            lbl, v = max(eps, key=lambda x: x[1])
            bits.append(f"{lbl} {v:.0f}%")
        sales = [("Sales Q/Q", self.sales_qoq), ("Sales 5y", self.sales_5y)]
        sales = [(lbl, v) for lbl, v in sales if v == v]
        if sales:
            lbl, v = max(sales, key=lambda x: x[1])
            bits.append(f"{lbl} {v:.0f}%")
        if self.triple_digit:
            bits.append("TRIPLE-DIGIT")
        if self.accelerating_proxy:
            bits.append("accel?(proxy)")
        return ", ".join(bits) if bits else "growth n/a"


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def assess_row(row: pd.Series) -> GrowthProfile:
    """Build a GrowthProfile from one normalized universe row (Section 3.4).

    Reads whatever growth columns are present; absent fields stay NaN and simply
    don't contribute to the flags (graceful degradation, Section 9).
    """
    f = CONFIG.fundamentals
    eps_qoq = _f(row.get("eps_qoq"))
    sales_qoq = _f(row.get("sales_qoq"))
    eps_5y = _f(row.get("eps_past5y"))
    sales_5y = _f(row.get("sales_past5y"))
    eps_this_y = _f(row.get("eps_this_y"))

    prof = GrowthProfile(
        eps_qoq=eps_qoq, eps_annual_5y=eps_5y, sales_qoq=sales_qoq,
        sales_5y=sales_5y, eps_this_y=eps_this_y,
    )

    # Magnitude gates — use the best available EPS signal (QoQ preferred, then
    # this-year, then 5yr) and the best available sales signal.
    best_eps = np.nanmax([eps_qoq, eps_this_y]) if not (np.isnan(eps_qoq) and np.isnan(eps_this_y)) else np.nan
    best_sales = np.nanmax([sales_qoq, sales_5y]) if not (np.isnan(sales_qoq) and np.isnan(sales_5y)) else np.nan

    prof.meets_eps_qoq = bool(best_eps >= f.min_eps_qoq_growth) if best_eps == best_eps else False
    prof.meets_annual_eps = bool(eps_5y >= f.min_annual_eps_growth) if eps_5y == eps_5y else False
    prof.meets_sales = bool(best_sales >= f.min_sales_growth) if best_sales == best_sales else False

    # Triple-digit elite signal.
    candidates = [v for v in (eps_qoq, eps_this_y, sales_qoq, sales_5y) if v == v]
    prof.triple_digit = any(v >= f.triple_digit for v in candidates)

    prof.strong_growth = bool(prof.meets_eps_qoq or prof.meets_annual_eps or prof.meets_sales)

    # Acceleration PROXY: current-quarter EPS growth clearly exceeds the longer
    # annual rate -> growth is speeding up. Labeled a proxy because we lack the
    # full quarter-by-quarter series (Section 3.13).
    if eps_qoq == eps_qoq and eps_5y == eps_5y and eps_5y > 0:
        prof.accelerating_proxy = bool(eps_qoq >= eps_5y * 1.3)
        if prof.accelerating_proxy:
            prof.notes.append("EPS QoQ >> 5yr rate (acceleration proxy)")

    if prof.triple_digit:
        prof.notes.append("triple-digit growth (elite signal)")
    return prof
