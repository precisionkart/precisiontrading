"""rs_rating.py — RS Rating PROXY, 1-99 (spec Sections 3.3 / 3.9).

The real IBD RS Rating is proprietary and cannot be reproduced. This computes a
clearly-labeled PROXY: a front-weighted trailing total return, then ranked into
a 1-99 percentile across the supplied universe. Higher = stronger.

IMPORTANT (Section 1 guardrail): this is a PROXY, not IBD's RS Rating. Every
output that carries it must label it as such. RS is a *percentile across a
universe*, so it is meaningless for a handful of tickers — callers must pass a
broad reference universe (see analyzer / store for the My-Picks case).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Front-weighted blend of trailing performance windows (IBD-style emphasis on
# the most recent quarter). Weights need not sum to 1; they are relative.
DEFAULT_WEIGHTS: dict[str, float] = {
    "perf_quarter": 0.40,   # most recent ~13 weeks, weighted heaviest
    "perf_half": 0.20,
    "perf_year": 0.20,
    "perf_month": 0.20,
}

PROXY_LABEL = "RS proxy (percentile of front-weighted trailing return; NOT IBD RS Rating)"


def weighted_return(perf: pd.DataFrame, weights: dict[str, float] = DEFAULT_WEIGHTS) -> pd.Series:
    """Combine available trailing-performance columns into one score per row.

    `perf` holds numeric percentage returns (already parsed via to_num). Missing
    columns/values are skipped and the remaining weights renormalised per row so
    a name with partial history still gets a fair composite.
    """
    cols = [c for c in weights if c in perf.columns]
    if not cols:
        return pd.Series(np.nan, index=perf.index)

    w = pd.Series({c: weights[c] for c in cols}, dtype="float64")
    sub = perf[cols].astype("float64")
    mask = sub.notna()
    weight_matrix = mask.mul(w, axis=1)
    denom = weight_matrix.sum(axis=1)
    numer = (sub.fillna(0.0) * weight_matrix).sum(axis=1)
    out = numer / denom.replace(0.0, np.nan)
    return out


def rs_rating(scores: pd.Series) -> pd.Series:
    """Map raw composite returns to a 1-99 RS percentile across the universe.

    Ranks ascending so the strongest names get the highest numbers. NaN inputs
    stay NaN. With <2 valid names the percentile is undefined, so we return NaN
    (RS is meaningless for tiny samples — Section 7B RS-fidelity note).
    """
    valid = scores.dropna()
    if len(valid) < 2:
        return pd.Series(np.nan, index=scores.index)

    pct = valid.rank(method="average", pct=True)           # (0, 1]
    rating = np.ceil(pct * 99).clip(1, 99).astype("int64")
    out = pd.Series(np.nan, index=scores.index, dtype="float64")
    out.loc[rating.index] = rating.astype("float64")
    return out


def compute_rs(perf: pd.DataFrame, weights: dict[str, float] = DEFAULT_WEIGHTS) -> pd.Series:
    """Convenience: weighted_return -> rs_rating. Returns a 1-99 float Series."""
    return rs_rating(weighted_return(perf, weights))
