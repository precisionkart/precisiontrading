"""dashboard_logic.py — pure helpers for the Morning Dashboard (Phase 7.5).

No Streamlit import here, so this is unit-testable: the Top-10 builder, the
sector-filter, the RS tier colors, the pill pass/fail mapping, the sector-strength
rows (with change-vs-yesterday), and the template-generated technical-analysis
prose all live here.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

# RS tier -> (background, text) colors (spec Phase 7.5 point 5).
RS_TIERS = [
    (90, "#16A34A", "#FFFFFF"),
    (80, "#F59E0B", "#FFFFFF"),
    (70, "#737373", "#FFFFFF"),
    (0, "#D4D4D8", "#0A0A0A"),
]


def rs_tier(rs: float) -> tuple[str, str]:
    """Return (bg, fg) for an RS value's tier."""
    if rs is None or (isinstance(rs, float) and rs != rs):
        return "#D4D4D8", "#0A0A0A"
    for threshold, bg, fg in RS_TIERS:
        if rs >= threshold:
            return bg, fg
    return "#D4D4D8", "#0A0A0A"


def pill_kind(passed: bool) -> str:
    """'pass' (green) or 'fail' (red) — drives the pill CSS class."""
    return "pass" if passed else "fail"


PILL_COLORS = {
    "pass": {"bg": "#DCFCE7", "border": "#16A34A", "fg": "#14532D"},
    "fail": {"bg": "#FEE2E2", "border": "#DC2626", "fg": "#7F1D1D"},
}


# ---------------------------------------------------------------------------
# Top 10 (Focus + Watch fill).
# ---------------------------------------------------------------------------
TOP10_COLUMNS = ["Rank", "Ticker", "Sector", "Score", "RS", "Pattern", "R:R", "Source"]


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def build_top10(focus: Optional[pd.DataFrame], targets: Optional[pd.DataFrame],
                sector_filter: Optional[str] = None, n: int = 10) -> pd.DataFrame:
    """Combine Focus (Source='Focus') with the top Targets (Source='Watch') into a
    single ranked table of `n` rows, sorted by score desc. Optional sector filter.
    """
    rows = []
    seen = set()
    if focus is not None and len(focus):
        for _, r in focus.iterrows():
            tk = str(r.get("ticker"))
            seen.add(tk)
            rows.append({"Ticker": tk, "Sector": r.get("sector") or "",
                         "Score": _num(r.get("pinpoint_score")), "RS": _num(r.get("rs")),
                         "Pattern": str(r.get("pattern") or "—").split(" /")[0],
                         "R:R": _num(r.get("reward_risk")), "Source": "Focus"})
    if targets is not None and len(targets):
        for _, r in targets.iterrows():
            tk = str(r.get("ticker"))
            if tk in seen:
                continue
            rows.append({"Ticker": tk, "Sector": r.get("sector") or "",
                         "Score": _num(r.get("pinpoint_score")), "RS": _num(r.get("rs")),
                         "Pattern": "—", "R:R": None, "Source": "Watch"})

    df = pd.DataFrame(rows)
    if len(df) == 0:
        return pd.DataFrame(columns=TOP10_COLUMNS)
    if sector_filter:
        df = df[df["Sector"] == sector_filter]
    df = df.sort_values("Score", ascending=False, na_position="last").head(n).reset_index(drop=True)
    df.insert(0, "Rank", range(1, len(df) + 1))
    return df[TOP10_COLUMNS]


# ---------------------------------------------------------------------------
# Sector strength (with change vs the most recent prior history entry).
# ---------------------------------------------------------------------------
def prior_theme_scores(history: Optional[dict]) -> dict:
    """From theme_history.json {week: {theme: {rank,score}}}, return the most
    recent PRIOR entry's {theme: score} (empty if <2 entries)."""
    if not history or len(history) < 2:
        return {}
    keys = sorted(history.keys())
    prev = history[keys[-2]]
    return {t: rec.get("score") for t, rec in prev.items()}


def sector_strength_rows(themes: list, prior_scores: Optional[dict] = None,
                         hot_frac: float = 0.30) -> list[dict]:
    """Rows for the Sector Strength widget: theme, score, delta vs prior, hot flag."""
    if not themes:
        return []
    prior_scores = prior_scores or {}
    n = len(themes)
    hot_cut = max(1, math.ceil(hot_frac * n))
    out = []
    for t in themes:
        name = t.get("theme")
        score = t.get("score")
        rank = t.get("rank")
        prev = prior_scores.get(name)
        delta = (score - prev) if (prev is not None and score is not None) else None
        out.append({"Sector": name, "RS Score": score, "Δ": delta,
                    "rank": rank, "hot": bool(rank is not None and rank <= hot_cut)})
    return sorted(out, key=lambda r: (r["rank"] is None, r["rank"]))


def delta_str(delta: Optional[float]) -> str:
    if delta is None:
        return "—"
    arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "•")
    return f"{arrow} {abs(delta):.1f}"


# ---------------------------------------------------------------------------
# Technical-analysis prose (template, no LLM).
# ---------------------------------------------------------------------------
def technical_analysis(pr) -> str:
    """Generate a short prose summary from a PickResult's fired layers/values."""
    tk = pr.ticker
    stage = (pr.stage or "").replace(" (advancing)", "")
    bits = []

    lead = f"{tk} is a {stage} leader" if "Stage 2" in (pr.stage or "") else f"{tk} is in {stage or 'an early base'}"
    if pr.pattern:
        patt = pr.pattern.split(" /")[0].lower()
        near = " near 52-week highs" if _passed(pr, "near_high") else ""
        bits.append(f"{lead} in a {patt}{near}.")
    else:
        bits.append(f"{lead}; no actionable pattern has formed yet.")

    if pr.rvol == pr.rvol:
        vol = "confirming" if _passed(pr, "volume") else "light"
        bits.append(f"Volume is {vol} with RVOL {pr.rvol:.1f}.")

    if pr.measured_move_pct is not None and pr.reward_risk is not None:
        floor = "well above" if pr.reward_risk >= 5 else "below"
        bits.append(f"The pattern's prior advance projects a {pr.measured_move_pct:.0f}% "
                    f"measured move from the breakout, for R:R {pr.reward_risk:.1f}:1 — "
                    f"{floor} the 5:1 floor.")

    if pr.theme and pr.theme_rank is not None:
        if _passed(pr, "hot_theme"):
            score_txt = f", RS {pr.theme_score:.1f}" if pr.theme_score is not None else ""
            bits.append(f"The {_theme_name(pr.theme)} theme is hot today "
                        f"(#{pr.theme_rank}{score_txt}), adding sector tailwind.")
        else:
            bits.append(f"The {_theme_name(pr.theme)} theme is mid-pack today "
                        f"(#{pr.theme_rank}), so there's no strong sector tailwind.")
    return " ".join(bits)


def _theme_name(theme_label: str) -> str:
    return theme_label.split(" #")[0] if theme_label else theme_label


def _passed(pr, key: str) -> bool:
    for c in getattr(pr, "criteria", []):
        if c.key == key:
            return c.passed
    return False
