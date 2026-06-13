"""dashboard_logic.py — pure helpers for the Morning Dashboard (Phase 7.5).

No Streamlit import here, so this is unit-testable: the Top-10 builder, the
sector-filter, the RS tier colors, the pill pass/fail mapping, the sector-strength
rows (with change-vs-yesterday), and the template-generated technical-analysis
prose all live here.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("pinpoint")

# A podium card is labeled "BEST SETUP TODAY" — it must be a genuine setup, so
# only Tier 1 (>=80) / Tier 2 (>=65) names with a full enriched trade plan
# qualify. Never put a sub-Tier-2 score or an unenriched row in a podium card.
PODIUM_MIN_SCORE = 65.0


def _plan_value(row, *keys):
    """First non-null/finite numeric among `keys` (e.g. measured_target then
    target_5r), else None."""
    for k in keys:
        v = row.get(k)
        if isinstance(v, (int, float)) and v == v:
            return float(v)
    return None


def build_podium(focus, min_score: float = PODIUM_MIN_SCORE) -> list:
    """Qualifying podium setups: score >= min_score (Tier 1/2) AND fully enriched
    (entry, stop, R:R, and a target). Names that clear the score gate but lack a
    trade plan are excluded with a logged warning. Sorted earnings-flag first,
    then score. Returns up to 3 podium-row dicts."""
    if focus is None or len(focus) == 0 or "pinpoint_score" not in getattr(focus, "columns", []):
        return []
    df = focus.copy()
    if "earnings_flag_active" in df.columns:
        df = df.sort_values(["earnings_flag_active", "pinpoint_score"], ascending=False)
    else:
        df = df.sort_values("pinpoint_score", ascending=False)

    out = []
    for _, r in df.iterrows():
        score = r.get("pinpoint_score")
        if not (isinstance(score, (int, float)) and score == score and score >= min_score):
            continue                                   # below the Tier-2 floor
        entry = _plan_value(r, "entry_trigger", "entry")
        stop = _plan_value(r, "stop")
        rr = _plan_value(r, "reward_risk")
        target = _plan_value(r, "measured_target", "target_5r", "target")
        if entry is None or stop is None or rr is None or target is None:
            logger.warning("podium: excluding %s (score %.1f) — unenriched / no "
                           "trade plan (entry=%s stop=%s rr=%s target=%s)",
                           r.get("ticker"), score, entry, stop, rr, target)
            continue
        out.append({"ticker": r.get("ticker"), "sector": r.get("sector"),
                    "score": score, "rs": r.get("rs"), "pattern": r.get("pattern"),
                    "entry": entry, "stop": stop, "target": target, "reward_risk": rr,
                    "earnings_flag": bool(r.get("earnings_flag_active"))})
        if len(out) == 3:
            break
    return out

# RS tier -> (background, text) colors (Phase 7.6 fluoro).
RS_TIERS = [
    (90, "#00D964", "#062B16"),     # fluoro green, dark text
    (80, "#FFB800", "#3D2C00"),     # amber, dark text
    (70, "#6B7280", "#FFFFFF"),     # gray, white text
    (0, "#D4D4D8", "#0A0A0A"),      # light gray, dark text
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


# fluoro pill colors (light-mode backgrounds)
PILL_COLORS = {
    "pass": {"bg": "#E6FFF1", "border": "#00D964", "fg": "#046A38"},
    "fail": {"bg": "#FFE6EC", "border": "#FF3366", "fg": "#7F0820"},
}


# ---------------------------------------------------------------------------
# Top 10 (Focus + Watch fill).
# ---------------------------------------------------------------------------
TOP10_COLUMNS = ["Rank", "Ticker", "Sector", "Score", "RS", "Pattern", "R:R",
                 "Entry", "Stop", "Source"]


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
                         "R:R": _num(r.get("reward_risk")),
                         "Entry": _num(r.get("entry_trigger")), "Stop": _num(r.get("stop")),
                         "Source": "Focus"})
    if targets is not None and len(targets):
        for _, r in targets.iterrows():
            tk = str(r.get("ticker"))
            if tk in seen:
                continue
            rows.append({"Ticker": tk, "Sector": r.get("sector") or "",
                         "Score": _num(r.get("pinpoint_score")), "RS": _num(r.get("rs")),
                         "Pattern": "—", "R:R": None, "Entry": None, "Stop": None,
                         "Source": "Watch"})

    df = pd.DataFrame(rows)
    if len(df) == 0:
        return pd.DataFrame(columns=TOP10_COLUMNS)
    if sector_filter:
        df = df[df["Sector"] == sector_filter]
    df = df.sort_values("Score", ascending=False, na_position="last").head(n).reset_index(drop=True)
    df.insert(0, "Rank", range(1, len(df) + 1))
    return df[TOP10_COLUMNS]


def build_tiers(focus, targets, sector_filter=None):
    """All ranked names with a Tier label (Phase 10 step 6): Elite 80+, Good
    65-79, Watchlist 50-64. Names below 50 are dropped from the tiered view."""
    from pinpoint.scoring import tier as _tier_of
    df = build_top10(focus, targets, sector_filter, n=500)
    if len(df) == 0:
        return df.assign(Tier=[])
    df = df.copy()
    df["Tier"] = df["Score"].map(
        lambda s: _tier_of(float(s)) if s is not None and s == s else None)
    return df[df["Tier"].notna()].reset_index(drop=True)


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


def _lerp_hex(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
    br, bg, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
    return "#%02X%02X%02X" % (round(ar + (br - ar) * t), round(ag + (bg - ag) * t),
                              round(ab + (bb - ab) * t))


def tile_color(rs: Optional[float], smax: float) -> str:
    """Sector tile color: green for positive RS (intensity by magnitude), red for
    negative. Dark-enough green/red for white text — never near-black."""
    if rs is None or (isinstance(rs, float) and rs != rs) or smax <= 0:
        return "#6B7280"
    frac = max(-1.0, min(1.0, rs / smax))
    if frac >= 0:
        return _lerp_hex("#166534", "#00D964", frac)   # green-700 -> fluoro green
    return _lerp_hex("#166534", "#FF3366", -frac)        # toward red for negatives


def heatmap_tiles(rows: list) -> list[dict]:
    """Build tile descriptors (color + size weight + hover) for the CSS-grid
    sector heatmap. `rows` from treemap_data."""
    if not rows:
        return []
    smax = max((abs(r["score"]) for r in rows if r.get("score") is not None), default=1.0) or 1.0
    out = []
    for r in rows:
        out.append({
            "label": r["label"], "score": r.get("score"),
            "weight": r.get("value", 1.0), "color": tile_color(r.get("score"), smax),
            "hot": r.get("hot"), "delta": r.get("delta"), "top3": r.get("top3", "—"),
        })
    return out


def delta_str(delta: Optional[float]) -> str:
    if delta is None:
        return "—"
    arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "•")
    return f"{arrow} {abs(delta):.1f}"


# ---------------------------------------------------------------------------
# Mini sparkline (inline SVG, no chart engine) for the compact card row.
# ---------------------------------------------------------------------------
def sparkline_svg(closes, width: int = 72, height: int = 22, days: int = 60) -> str:
    """A tiny inline-SVG sparkline of the last `days` closes. Fluoro green if the
    window is up, fluoro red if down. Returns '' if there's too little data."""
    try:
        vals = [float(v) for v in list(closes)[-days:] if v == v]
    except (TypeError, ValueError):
        return ""
    if len(vals) < 3:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    n = len(vals)
    pad = 1.5
    color = "#00D964" if vals[-1] >= vals[0] else "#FF3366"
    pts = []
    for i, v in enumerate(vals):
        x = pad + (width - 2 * pad) * (i / (n - 1))
        y = pad + (height - 2 * pad) * (1.0 - (v - lo) / rng)
        pts.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(pts)
    return (f"<svg width='{width}' height='{height}' viewBox='0 0 {width} {height}' "
            f"fill='none' xmlns='http://www.w3.org/2000/svg'>"
            f"<polyline points='{poly}' stroke='{color}' stroke-width='1.4' "
            f"fill='none' stroke-linejoin='round' stroke-linecap='round'/></svg>")


# ---------------------------------------------------------------------------
# Sector treemap data (area = rank weight, color = RS score, hover detail).
# ---------------------------------------------------------------------------
def treemap_data(themes: list, top10: Optional[pd.DataFrame] = None,
                 prior_scores: Optional[dict] = None, hot_frac: float = 0.30) -> list[dict]:
    """Rows for the sector treemap: label, value (rank-based size), score (color),
    delta vs prior, hot flag, and the top-3 Top-10 stocks in that sector."""
    if not themes:
        return []
    prior_scores = prior_scores or {}
    n = len(themes)
    import math
    hot_cut = max(1, math.ceil(hot_frac * n))
    # top stocks per sector from the Top-10 table
    by_sector: dict[str, list[str]] = {}
    if top10 is not None and len(top10) and "Sector" in top10.columns:
        for _, r in top10.iterrows():
            by_sector.setdefault(str(r["Sector"]), []).append(str(r["Ticker"]))
    out = []
    for t in themes:
        name = t.get("theme")
        score = t.get("score")
        rank = t.get("rank")
        prev = prior_scores.get(name)
        delta = (score - prev) if (prev is not None and score is not None) else None
        out.append({
            "label": name, "score": score, "rank": rank,
            "value": float(n - rank + 1) if rank else 1.0,      # #1 biggest
            "delta": delta, "hot": bool(rank is not None and rank <= hot_cut),
            "top3": ", ".join(by_sector.get(name, [])[:3]) or "—",
        })
    return out


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


# ---------------------------------------------------------------------------
# Plain-English setup explanation (Phase 7.7) — one readable paragraph, bold
# numbers, no jargon (the pills cover the criteria).
# ---------------------------------------------------------------------------
def setup_explanation(pr) -> str:
    """A single reading-friendly paragraph (HTML with <b> on key numbers)."""
    tk = _html_b(pr.ticker)

    # Earnings flag is the headline when active (the spec's highest-edge setup).
    if getattr(pr, "earnings_flag_active", False):
        zone = getattr(pr, "earnings_flag_ema_zone", None)
        zone_txt = f" holding the <b>{zone} EMA</b>" if zone else ""
        gap = getattr(pr, "gap_pct", None)
        gtxt = f" <b>{gap:.1f}%</b>" if gap is not None else ""
        gd = getattr(pr, "gap_date", None)
        gdtxt = f" on <b>{gd}</b>" if gd else ""
        return (f"This is an <b>EARNINGS FLAG</b> setup — {tk} gapped up{gtxt}{gdtxt}, "
                f"consolidated{zone_txt}, and is breaking out today. Per the methodology, "
                f"this is the highest-edge pattern in the strategy.")
    theme = _theme_name(pr.theme) if pr.theme else None
    theme_tail = ""
    if theme and pr.theme_rank is not None:
        n = 10
        if _passed(pr, "hot_theme"):
            theme_tail = (f" The {theme} sector is hot today "
                          f"(<b>#{pr.theme_rank} of {n}</b>), adding tailwind.")
        else:
            theme_tail = (f" The {theme} sector is mid-pack today "
                          f"(#{pr.theme_rank} of {n}), so there's no strong tailwind.")

    if pr.entry and pr.reward_risk and pr.pattern:
        patt = pr.pattern.split(" /")[0].lower()
        weeks = max(1, round((pr.pattern_bars or 30) / 5))
        risk_pct = ((pr.entry - pr.stop) / pr.entry * 100.0) if pr.stop else float("nan")
        risk_txt = f" (a <b>{risk_pct:.1f}% risk</b>)" if risk_pct == risk_pct else ""
        return (f"{tk} is forming a <b>{patt}</b> that's been tightening for about "
                f"<b>{weeks} weeks</b>. The setup triggers on a breakout above "
                f"<b>${pr.entry:,.2f}</b> on confirming volume. Stop sits below the recent "
                f"low at <b>${pr.stop:,.2f}</b>{risk_txt}. First target is "
                f"<b>${pr.target:,.2f}</b> — a <b>{pr.reward_risk:.1f}:1</b> reward-to-risk "
                f"ratio based on the prior advance.{theme_tail}")

    # no actionable trigger yet
    fails = [c.label for c in getattr(pr, "criteria", []) if not c.passed]
    stage = (pr.stage or "").replace(" (advancing)", "")
    if pr.pattern is None:
        base = (f"{tk} is {('a ' + stage + ' name') if stage else 'on the radar'} but isn't "
                f"in an actionable setup yet — no valid pattern has formed.")
    else:
        base = (f"{tk} shows a <b>{pr.pattern.split(' /')[0].lower()}</b>, but the "
                f"reward-to-risk isn't there yet at current prices.")
    if fails:
        base += f" It currently falls short on: <b>{', '.join(fails[:3])}</b>."
    return base + theme_tail


def _html_b(s) -> str:
    return f"<b>{s}</b>"


# ---------------------------------------------------------------------------
# TradingView watchlist import string (Phase 7.7).
# ---------------------------------------------------------------------------
def tv_string(tickers, exchange_map: Optional[dict] = None,
              default_exchange: str = "NASDAQ") -> str:
    """EXCHANGE:TICKER comma list for pasting into a TradingView watchlist.
    Exchange is looked up in `exchange_map` (we don't capture exchange from
    Finviz, so it falls back to NASDAQ — TradingView resolves most either way)."""
    exchange_map = exchange_map or {}
    out = []
    for t in tickers:
        if t is None:
            continue
        t = str(t).strip().upper()
        if not t or t == "NONE":
            continue
        out.append(f"{exchange_map.get(t, default_exchange)}:{t}")
    return ",".join(out)
