"""themes.py — sector/theme rotation + industry-group RS (spec Section 3.10).

The biggest yearly gainers come from the hottest theme (AI, semis, nuclear, ...).
Before name-level scanning we rank theme ETFs by relative strength and rank
Finviz industries by RS, then:
  * fire the `hot_theme` layer when a stock's theme is in the top 30% of themes,
  * fire the `top_industry_group` layer when its Finviz industry is in the top
    10% of industries.

Theme RS is computed with the SAME proxy used for stocks (a front-weighted
trailing return), so the ranking is consistent and transparent. Industry RS uses
Finviz's group performance screener (group.Industry()).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date as date_cls
from typing import Optional

import numpy as np
import pandas as pd

from .config import CONFIG, THEME_ETFS, HOT_THEME_TOP_FRAC, TOP_INDUSTRY_FRAC
from . import ohlcv as ohlcv_mod
from .rs_rating import weighted_return

# Broad SPDR sector ETF per Finviz-style sector name — the OHLCV proxy used to
# rank sector "groups" now that Finviz's industry group screener is gone. The
# stock's mapped sector resolves to one of these for the top-group RS check.
SECTOR_ETFS: dict[str, str] = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Healthcare": "XLV",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Communication Services": "XLC",
}

logger = logging.getLogger("pinpoint.themes")

# FUTURE-WATCH (instrumentation, not action): the theme baskets here are ETF
# proxies. If a theme (notably Uranium/Nuclear: URNM/NLR/URA) sits at the bottom
# of the rankings for 4+ consecutive weeks WHILE individual names in that theme
# are clearly leading, the basket likely needs refining — some nuclear plays are
# classified under Industrials/Utilities and aren't captured by these ETFs.
# `log_theme_history()` records weekly rankings to data/theme_history.json so we
# can see this drift over time; review it before editing THEME_ETFS in config.

# Specialized industries -> theme (keyword match on the Finviz industry string).
INDUSTRY_THEME_KEYWORDS: list[tuple[str, str]] = [
    ("semiconductor", "Semiconductors"),
    ("uranium", "Uranium/Nuclear"),
    ("solar", "Solar"),
]
# Nuclear-leaning utilities (independent power producers) -> Uranium/Nuclear.
INDUSTRY_THEME_KEYWORDS += [("independent power", "Uranium/Nuclear")]

# Finviz sector -> theme key (broad fallback when no specialized industry match).
SECTOR_THEME_MAP: dict[str, str] = {
    "Technology": "Technology",
    "Energy": "Energy",
    "Financial": "Financials",
    "Financial Services": "Financials",
    "Consumer Cyclical": "Consumer Cyclical",
    "Healthcare": "Healthcare",
    "Industrials": "Industrials",
}


def _safe_str(x) -> str:
    """NA/None-safe string (snapshot string columns use pandas <NA>, which raises
    in a boolean `or` context)."""
    try:
        if x is None or pd.isna(x):
            return ""
    except (TypeError, ValueError):
        pass
    return str(x)


def _trailing_perf(close: pd.Series) -> dict[str, float]:
    """Trailing % returns from a daily close series (windows in trading days)."""
    def r(n: int) -> float:
        if len(close) <= n:
            return float("nan")
        return (close.iloc[-1] / close.iloc[-n] - 1.0) * 100.0
    return {"perf_month": r(21), "perf_quarter": r(63),
            "perf_half": r(126), "perf_year": r(252)}


@dataclass
class ThemeContext:
    theme_rank: dict[str, dict] = field(default_factory=dict)      # theme -> {rank,pct,score}
    industry_rank: dict[str, dict] = field(default_factory=dict)   # industry -> {rank,pct}
    n_themes: int = 0
    n_industries: int = 0
    warnings: list[str] = field(default_factory=list)

    # -- mapping --
    def theme_for(self, sector: Optional[str], industry: Optional[str]) -> Optional[str]:
        ind = _safe_str(industry).lower()
        for kw, theme in INDUSTRY_THEME_KEYWORDS:
            if kw in ind and theme in self.theme_rank:
                return theme
        return SECTOR_THEME_MAP.get(_safe_str(sector).strip())

    def theme_label(self, sector: Optional[str], industry: Optional[str]) -> str:
        theme = self.theme_for(sector, industry)
        if theme and theme in self.theme_rank:
            return f"{theme} #{self.theme_rank[theme]['rank']}"
        return ""

    # -- layer predicates --
    def is_hot_theme(self, sector: Optional[str], industry: Optional[str],
                     top_frac: float = HOT_THEME_TOP_FRAC) -> bool:
        theme = self.theme_for(sector, industry)
        if not theme or theme not in self.theme_rank:
            return False
        return self.theme_rank[theme]["pct"] >= (1.0 - top_frac)

    def is_top_industry(self, industry: Optional[str],
                        top_frac: float = TOP_INDUSTRY_FRAC) -> bool:
        rec = self.industry_rank.get(_safe_str(industry).strip())
        return bool(rec and rec["pct"] >= (1.0 - top_frac))

    def top_themes(self, n: int = 5) -> list[tuple[str, dict]]:
        return sorted(self.theme_rank.items(), key=lambda kv: kv[1]["rank"])[:n]

    def top_industries(self, n: int = 5) -> list[tuple[str, dict]]:
        return sorted(self.industry_rank.items(), key=lambda kv: kv[1]["rank"])[:n]


# ---------------------------------------------------------------------------
# Builders.
# ---------------------------------------------------------------------------
def rank_themes(ohlcv_provider=None) -> tuple[dict[str, dict], list[str]]:
    """Rank theme ETFs by the stock RS proxy (front-weighted trailing return).

    A theme's score is the mean weighted-return of its member ETFs; themes are
    then percentile-ranked. Returns (theme_rank, warnings)."""
    if ohlcv_provider is None:
        def ohlcv_provider(t):
            return ohlcv_mod.fetch_daily(t).df

    warnings: list[str] = []
    scores: dict[str, float] = {}
    for theme, etfs in THEME_ETFS.items():
        vals = []
        for etf in etfs:
            daily = ohlcv_provider(etf)
            if daily is None or len(daily) < 30:
                warnings.append(f"no OHLCV for theme ETF {etf}")
                continue
            perf = pd.DataFrame([_trailing_perf(daily["Close"])])
            wr = weighted_return(perf).iloc[0]
            if wr == wr:
                vals.append(wr)
        if vals:
            scores[theme] = float(np.mean(vals))

    if not scores:
        return {}, warnings
    s = pd.Series(scores)
    pct = s.rank(pct=True)
    order = s.rank(ascending=False, method="min").astype(int)
    theme_rank = {t: {"score": round(s[t], 2), "pct": float(pct[t]),
                      "rank": int(order[t])} for t in s.index}
    return theme_rank, warnings


def rank_industries(client=None, ohlcv_provider=None) -> tuple[dict[str, dict], list[str]]:
    """Rank the broad SPDR SECTOR ETFs by RS proxy from OHLCV, keyed by Finviz
    sector name (Task 4 — Massive has no industry-group screener).

    The returned dict is keyed by SECTOR; the stock's mapped sector resolves to
    its rank for the top-group RS gate. `client` is ignored (kept for call-site
    compatibility)."""
    if ohlcv_provider is None:
        def ohlcv_provider(t):
            return ohlcv_mod.fetch_daily(t).df

    warnings: list[str] = []
    scores: dict[str, float] = {}
    for sector, etf in SECTOR_ETFS.items():
        daily = ohlcv_provider(etf)
        if daily is None or len(daily) < 30:
            warnings.append(f"no OHLCV for sector ETF {etf} ({sector})")
            continue
        perf = pd.DataFrame([_trailing_perf(daily["Close"])])
        wr = weighted_return(perf).iloc[0]
        if wr == wr:
            scores[sector] = float(wr)
    if len(scores) < 2:
        return {}, warnings + ["insufficient sector ETF performance data"]
    s = pd.Series(scores)
    pct = s.rank(pct=True)
    order = s.rank(ascending=False, method="min").astype(int)
    industry_rank = {sec: {"pct": float(pct[sec]), "rank": int(order[sec]),
                           "score": round(s[sec], 2)} for sec in s.index}
    return industry_rank, warnings


def context_from_list(themes: list) -> ThemeContext:
    """Rebuild a (theme-only) ThemeContext from a serialized [{theme,rank,score}]
    list — used by the dashboard when only the cached/published rankings are
    available (no live ETF pull). Industry rank is empty in this mode."""
    n = len(themes or [])
    rank = {}
    for t in (themes or []):
        r = t.get("rank")
        pct = (1.0 - (r - 1) / n) if (n > 1 and r) else 1.0
        rank[t.get("theme")] = {"rank": r, "score": t.get("score"), "pct": pct}
    return ThemeContext(theme_rank=rank, industry_rank={}, n_themes=n, n_industries=0)


def build_theme_context(client=None, ohlcv_provider=None) -> ThemeContext:
    """Full theme + industry ranking context (3.10)."""
    theme_rank, tw = rank_themes(ohlcv_provider)
    industry_rank, iw = rank_industries(client)
    return ThemeContext(theme_rank=theme_rank, industry_rank=industry_rank,
                        n_themes=len(theme_rank), n_industries=len(industry_rank),
                        warnings=tw + iw)


def log_theme_history(theme_ctx: ThemeContext, when: Optional[str] = None) -> Optional[str]:
    """Append the week's theme rankings to data/theme_history.json (keyed by
    ISO-week) so basket drift is visible over time. Best-effort; never raises."""
    if theme_ctx is None or not theme_ctx.theme_rank:
        return None
    path = os.path.join(CONFIG.paths.data_dir, "theme_history.json")
    try:
        history = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                history = json.load(f)
        if when is None:
            iso = date_cls.today().isocalendar()
            when = f"{iso[0]}-W{iso[1]:02d}"          # one entry per ISO week
        history[when] = {t: {"rank": r["rank"], "score": r["score"]}
                         for t, r in theme_ctx.theme_rank.items()}
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not log theme history: %s", exc)
        return None
