"""charts.py — annotated candlestick charts (spec Section 7 + product brief).

Renders a product-grade chart per Focus name: a daily panel (75% height) with
5/10/20 EMA + 50/200 SMA, a manual volume sub-panel, the detected pattern shaded,
and the entry / .89 stop / measured target drawn as labelled dashed lines with a
score badge; plus a weekly context panel (25% height) showing price + 10-wk EMA +
30-wk SMA. A simpler single-panel renderer backs the on-demand `--chart` flag.

Styling follows the brief exactly: white background, subtle horizontal-only
gridlines, deep green/red candles, thin bodies, 30%-opacity volume, the specified
MA colors, month-only date axis, Inter/system-sans font with tabular figures.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import matplotlib
matplotlib.use("Agg")                       # headless / server-safe
import matplotlib.dates as mdates           # noqa: E402
import matplotlib.font_manager as fm        # noqa: E402
import matplotlib.pyplot as plt             # noqa: E402
import mplfinance as mpf                    # noqa: E402
import numpy as np                          # noqa: E402
import pandas as pd                         # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

from .config import CONFIG
from .ohlcv import add_moving_averages, to_weekly

logger = logging.getLogger("pinpoint.charts")

# --- palette (shared via chart_config so the two renderers can't drift) -----
from . import chart_config as _cc   # noqa: E402

WHITE = _cc.WHITE
GRID = _cc.GRID
UP = _cc.UP
DOWN = _cc.DOWN
EMA5 = _cc.MA_COLORS["EMA5"]
EMA10 = _cc.MA_COLORS["EMA10"]
EMA20 = _cc.MA_COLORS["EMA20"]
SMA50 = _cc.MA_COLORS["SMA50"]
SMA200 = _cc.MA_COLORS["SMA200"]
TARGET_GRAY = _cc.TARGET_COLOR
TEXT = _cc.TEXT
SUBTLE = _cc.SUBTLE


def _setup_fonts() -> str:
    """Prefer Inter if available; fall back to a clean system sans. Enable
    tabular figures where the font supports them."""
    available = {f.name for f in fm.fontManager.ttflist}
    for fam in ("Inter", "Helvetica Neue", "Arial", "DejaVu Sans"):
        if fam in available:
            chosen = fam
            break
    else:
        chosen = "DejaVu Sans"
    matplotlib.rcParams.update({
        "font.family": chosen,
        "font.size": 9,
        "axes.edgecolor": "#E5E5E5",
        "axes.linewidth": 0.8,
        "text.color": TEXT,
        "axes.labelcolor": TEXT,
        "xtick.color": SUBTLE,
        "ytick.color": SUBTLE,
        "figure.dpi": 130,
    })
    return chosen


def _mpf_style():
    """Custom mplfinance marketcolors + style matching the brief."""
    mc = mpf.make_marketcolors(
        up=UP, down=DOWN, edge="inherit",
        wick={"up": UP, "down": DOWN}, volume="#9CA3AF",
    )
    return mpf.make_mpf_style(
        marketcolors=mc, facecolor=WHITE, figcolor=WHITE,
        gridcolor=GRID, gridstyle="-", gridaxis="horizontal", y_on_right=True,
        rc={"axes.spines.top": False, "axes.spines.right": False,
            "axes.spines.left": False},
    )


def _month_ticks(ax, index: pd.DatetimeIndex) -> None:
    """Place ticks at month starts only, labelled with the month abbrev (and
    year in January), against the integer x-positions mplfinance uses."""
    months = pd.Series(index).dt.to_period("M")
    positions, labels = [], []
    prev = None
    for i, per in enumerate(months):
        if per != prev:
            positions.append(i)
            ts = index[i]
            labels.append(ts.strftime("%b") if ts.month != 1 else ts.strftime("%b\n%Y"))
            prev = per
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, fontsize=8)
    ax.tick_params(axis="x", length=0)


def _draw_volume(ax, df: pd.DataFrame) -> None:
    """Manual volume bars: per-bar up/down color at 30% opacity, no spines."""
    colors = np.where(df["Close"].to_numpy() >= df["Open"].to_numpy(), UP, DOWN)
    ax.bar(range(len(df)), df["Volume"].to_numpy(), width=0.7, color=colors, alpha=0.30,
           linewidth=0)
    ax.set_ylabel("Vol", fontsize=7, color=SUBTLE)
    ax.margins(x=0.01)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_yticks([])
    ax.grid(False)


def _ma_addplots(df: pd.DataFrame, ax) -> list:
    specs = [("EMA5", EMA5, 1.5), ("EMA10", EMA10, 1.5), ("EMA20", EMA20, 1.5),
             ("SMA50", SMA50, 1.0), ("SMA200", SMA200, 1.5)]
    out = []
    for col, color, width in specs:
        if col in df.columns and df[col].notna().any():
            out.append(mpf.make_addplot(df[col], ax=ax, color=color, width=width))
    return out


def _hline(ax, y: float, color: str, label: str, xspan_frac: float = 0.6) -> None:
    """Dashed horizontal line across the last `xspan_frac` of the axis, with a
    right-edge label."""
    x0, x1 = ax.get_xlim()
    start = x1 - (x1 - x0) * xspan_frac
    ax.plot([start, x1], [y, y], color=color, ls="--", lw=1.0, zorder=5)
    ax.text(x1, y, f" {label}", va="center", ha="left", fontsize=7.5,
            color=color, fontweight="medium", clip_on=False)


def _score_badge(ax, score: float, rs: float) -> None:
    """Thin-bordered rounded rectangle, top-right, white fill so it reads over
    gridlines; positioned clear of the target line."""
    txt = f"Score {score:.1f}   RS {rs:.0f}" if rs == rs else f"Score {score:.1f}"
    box = FancyBboxPatch((0.77, 0.855), 0.21, 0.058, transform=ax.transAxes,
                         boxstyle="round,pad=0.008,rounding_size=0.02",
                         facecolor=WHITE, edgecolor="#D4D4D4", linewidth=1.0, zorder=10)
    ax.add_patch(box)
    ax.text(0.875, 0.884, txt, transform=ax.transAxes, ha="center", va="center",
            fontsize=8, color=TEXT, zorder=11)


@dataclass
class ChartAnnotation:
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    reward_risk: Optional[float] = None
    pattern_label: Optional[str] = None
    pattern_bars: int = 0
    score: Optional[float] = None
    rs: Optional[float] = None


def _build_focus_fig(ticker: str, daily: pd.DataFrame, ann: ChartAnnotation,
                     display_bars: int = 140):
    """Build the annotated two-panel figure (shared by the file/bytes renderers)."""
    _setup_fonts()
    style = _mpf_style()
    d_full = add_moving_averages(daily)
    d = d_full.iloc[-display_bars:].copy()
    w = add_moving_averages(to_weekly(daily), ema_spans=(10,), sma_spans=(30,))
    w = w.iloc[-60:].copy()

    fig = plt.figure(figsize=(10.5, 8.2))
    ax_price = fig.add_axes([0.07, 0.46, 0.86, 0.48])
    ax_vol = fig.add_axes([0.07, 0.345, 0.86, 0.10], sharex=ax_price)
    ax_week = fig.add_axes([0.07, 0.07, 0.86, 0.20])

    # --- daily price + MAs ---
    aps = _ma_addplots(d, ax_price)
    mpf.plot(d, type="candle", style=style, ax=ax_price, addplot=aps,
             update_width_config={"candle_linewidth": 0.7, "candle_width": 0.6})
    ax_price.set_ylabel("")
    ax_price.grid(axis="y", color=GRID, linewidth=0.5)
    ax_price.tick_params(axis="x", labelbottom=False, length=0)

    # pattern shaded region (last `pattern_bars` of the displayed window)
    if ann.pattern_bars and ann.pattern_bars > 0:
        n = len(d)
        x0 = max(0, n - ann.pattern_bars)
        ax_price.axvspan(x0 - 0.5, n - 0.5, color=UP, alpha=0.08, zorder=0)
        if ann.pattern_label:
            ytop = d["High"].iloc[x0:].max()
            ax_price.text(x0, ytop, f" {ann.pattern_label} — {ann.pattern_bars}d",
                          fontsize=7.5, color="#15803D", va="bottom", ha="left")

    # annotation lines
    if ann.entry is not None and ann.entry == ann.entry:
        _hline(ax_price, ann.entry, UP, f"Entry ${ann.entry:.2f}")
    if ann.stop is not None and ann.stop == ann.stop:
        _hline(ax_price, ann.stop, DOWN, f"Stop ${ann.stop:.2f} (.89)")
    if ann.target is not None and ann.target == ann.target:
        rr = f" (R:R {ann.reward_risk:.1f}:1)" if ann.reward_risk else ""
        _hline(ax_price, ann.target, TARGET_GRAY, f"Target ${ann.target:.2f}{rr}")

    if ann.score is not None:
        _score_badge(ax_price, ann.score, ann.rs if ann.rs is not None else float("nan"))

    # --- volume ---
    _draw_volume(ax_vol, d)
    _month_ticks(ax_vol, d.index)

    # --- weekly context ---
    ax_week.plot(range(len(w)), w["Close"].to_numpy(), color="#111827", lw=1.2)
    if "EMA10" in w:
        ax_week.plot(range(len(w)), w["EMA10"].to_numpy(), color=EMA10, lw=1.2)
    if "SMA30" in w:
        ax_week.plot(range(len(w)), w["SMA30"].to_numpy(), color=SMA200, lw=1.2)
    ax_week.grid(axis="y", color=GRID, linewidth=0.5)
    for s in ("top", "right", "left"):
        ax_week.spines[s].set_visible(False)
    ax_week.tick_params(length=0)
    ax_week.yaxis.tick_right()
    _month_ticks(ax_week, w.index)
    ax_week.text(0.0, 1.02, "Weekly", transform=ax_week.transAxes, fontsize=8,
                 color=SUBTLE, va="bottom")
    return fig


def render_focus_chart(ticker: str, daily: pd.DataFrame, out_path: str,
                       ann: ChartAnnotation, display_bars: int = 140) -> Optional[str]:
    """Render the annotated two-panel chart to `out_path`. Returns the path, or
    None on failure (never raises — Section 9)."""
    try:
        fig = _build_focus_fig(ticker, daily, ann, display_bars)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        fig.savefig(out_path, facecolor=WHITE, bbox_inches="tight")
        plt.close(fig)
        return out_path
    except Exception as exc:  # noqa: BLE001
        logger.warning("chart render failed for %s: %s", ticker, exc)
        plt.close("all")
        return None


def render_focus_png_bytes(ticker: str, daily: pd.DataFrame, ann: ChartAnnotation,
                           display_bars: int = 140) -> Optional[bytes]:
    """Render the annotated chart to PNG bytes in-memory (for st.image)."""
    import io
    try:
        fig = _build_focus_fig(ticker, daily, ann, display_bars)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=WHITE, bbox_inches="tight")
        plt.close(fig)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.warning("chart bytes render failed for %s: %s", ticker, exc)
        plt.close("all")
        return None


def render_simple_chart(ticker: str, daily: pd.DataFrame, out_path: str,
                        display_bars: int = 140) -> Optional[str]:
    """Single daily panel with MAs + volume (for the on-demand --chart flag)."""
    return render_focus_chart(ticker, daily, out_path,
                              ChartAnnotation(pattern_label=None), display_bars)
