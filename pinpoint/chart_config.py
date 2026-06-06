"""chart_config.py — single source of truth for chart styling (Phase 7.5).

Both renderers import from here so they can't drift:
  * charts.py        — mplfinance PNGs for the HTML report / CSV-XLSX paths
  * app/charts_plotly — interactive Plotly charts for the web dashboard

This module holds ONLY data (colors, MA periods, annotation geometry); it imports
nothing heavy so it's safe to use anywhere.
"""

from __future__ import annotations

from typing import Optional

# --- palette (product brief) ----------------------------------------------
WHITE = "#FFFFFF"
GRID = "#F3F4F6"
UP = "#16A34A"
DOWN = "#DC2626"
TEXT = "#0A0A0A"
SUBTLE = "#9CA3AF"
AXIS = "#A3A3A3"            # right-edge price scale (dashboard)

# moving-average colors (periods -> color). Widths are renderer-specific.
MA_COLORS: dict[str, str] = {
    "EMA5": "#A78BFA",     # purple — momentum
    "EMA10": "#3B82F6",    # blue — first support
    "EMA20": "#F59E0B",    # amber — the signal
    "SMA50": "#737373",    # gray — intermediate
    "SMA200": "#0A0A0A",   # near-black — macro anchor
}
EMA_SPANS = (5, 10, 20)
SMA_SPANS = (50, 200)

# weekly context overlays
WEEKLY_EMA = 10
WEEKLY_SMA = 30

# annotation line colors
ENTRY_COLOR = UP
STOP_COLOR = DOWN
TARGET_COLOR = "#737373"
PATTERN_FILL = "#16A34A"        # opacity applied per renderer (report 5-8%)


def annotation_lines(entry: Optional[float], stop: Optional[float],
                     target: Optional[float], reward_risk: Optional[float] = None):
    """Return the (label, price, color) tuples for the entry / .89 stop / target
    lines, skipping any that are missing. The dashboard renders these as a legend
    BELOW the chart (lines unlabeled on the plot); the report labels them inline.
    """
    out = []
    if entry is not None and entry == entry:
        out.append(("Entry", round(float(entry), 2), ENTRY_COLOR))
    if stop is not None and stop == stop:
        out.append(("Stop (.89)", round(float(stop), 2), STOP_COLOR))
    if target is not None and target == target:
        label = "Target"
        if reward_risk is not None and reward_risk == reward_risk:
            label = f"Target ({reward_risk:.1f}:1)"
        out.append((label, round(float(target), 2), TARGET_COLOR))
    return out
