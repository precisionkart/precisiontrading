"""charts_plotly.py — interactive dashboard charts (Phase 7.5).

Same palette / MA periods / annotation geometry as the report PNGs (shared via
pinpoint.chart_config), rendered as interactive Plotly figures: hover tooltips,
range slider, modebar PNG export. Annotation lines (entry / .89 stop / target)
are drawn UNLABELED on the chart; the labels + prices live in a legend rendered
below by the page. The score badge is gone — the chart is just the chart.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from pinpoint import chart_config as cc
from pinpoint import ohlcv as ohlcv_mod

# dashboard MA widths (thinner than the report; 200 SMA stays the anchor).
_MA_WIDTH = {"EMA5": 1.0, "EMA10": 1.0, "EMA20": 1.0, "SMA50": 1.0, "SMA200": 1.5}
_DISPLAY_BARS = 160


def _candles(d: pd.DataFrame) -> go.Candlestick:
    return go.Candlestick(
        x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"],
        name="Price", increasing_line_color=cc.UP, decreasing_line_color=cc.DOWN,
        increasing_fillcolor=cc.UP, decreasing_fillcolor=cc.DOWN,
        line_width=1, whiskerwidth=0.4, showlegend=False)


def _ma_traces(d: pd.DataFrame, cols) -> list:
    traces = []
    for col in cols:
        if col in d.columns and d[col].notna().any():
            traces.append(go.Scatter(
                x=d.index, y=d[col], mode="lines", name=col,
                line=dict(color=cc.MA_COLORS[col], width=_MA_WIDTH.get(col, 1.0)),
                hovertemplate=f"{col} %{{y:.2f}}<extra></extra>", showlegend=False))
    return traces


def _base_layout(height: int) -> dict:
    return dict(
        height=height, plot_bgcolor=cc.WHITE, paper_bgcolor=cc.WHITE,
        margin=dict(l=8, r=56, t=8, b=8), dragmode="zoom",
        font=dict(family="Inter, system-ui, sans-serif", color=cc.TEXT),
        hovermode="x unified",
        xaxis=dict(showgrid=False, color=cc.AXIS, tickfont=dict(size=10),
                   rangeslider=dict(visible=False)),
        yaxis=dict(side="right", showgrid=True, gridcolor=cc.GRID, nticks=6,
                   color=cc.AXIS, tickfont=dict(size=10), tickprefix="$"),
    )


def daily_figure(daily: pd.DataFrame, entry: Optional[float] = None,
                 stop: Optional[float] = None, target: Optional[float] = None,
                 pattern_bars: int = 0) -> Optional[go.Figure]:
    """Interactive daily chart: candles + 5 MAs + faint volume + unlabeled
    entry/stop/target lines + 5%-opacity pattern shading + range slider."""
    try:
        d = ohlcv_mod.add_moving_averages(daily).iloc[-_DISPLAY_BARS:]
        if len(d) == 0:
            return None
        fig = go.Figure()
        fig.add_trace(_candles(d))
        for tr in _ma_traces(d, ("EMA5", "EMA10", "EMA20", "SMA50", "SMA200")):
            fig.add_trace(tr)

        # faint volume on a secondary axis, parked in the lower band.
        vmax = float(d["Volume"].max()) if "Volume" in d.columns else 0.0
        if vmax > 0:
            colors = np.where(d["Close"] >= d["Open"], cc.UP, cc.DOWN)
            fig.add_trace(go.Bar(x=d.index, y=d["Volume"], marker_color=colors,
                                 marker_line_width=0, opacity=0.22, yaxis="y2",
                                 name="Vol", hovertemplate="Vol %{y:,.0f}<extra></extra>",
                                 showlegend=False))

        layout = _base_layout(height=440)
        layout["yaxis2"] = dict(overlaying="y", side="left", showgrid=False,
                                range=[0, vmax * 4.0 if vmax else 1], visible=False)
        layout["xaxis"]["rangeslider"] = dict(visible=True, thickness=0.06)
        fig.update_layout(**layout)

        # unlabeled annotation lines
        for _label, price, color in cc.annotation_lines(entry, stop, target):
            fig.add_hline(y=price, line_dash="dash", line_color=color, line_width=1)

        # pattern shading (5% opacity) over the last `pattern_bars`
        if pattern_bars and pattern_bars > 1 and len(d) > pattern_bars:
            fig.add_vrect(x0=d.index[-pattern_bars], x1=d.index[-1],
                          fillcolor=cc.PATTERN_FILL, opacity=0.05, line_width=0)
        return fig
    except Exception:  # noqa: BLE001
        return None


def weekly_figure(daily: pd.DataFrame) -> Optional[go.Figure]:
    """Weekly context: candles + 10-wk EMA + 30-wk SMA, no annotations."""
    try:
        w = ohlcv_mod.add_moving_averages(ohlcv_mod.to_weekly(daily),
                                          ema_spans=(cc.WEEKLY_EMA,), sma_spans=(cc.WEEKLY_SMA,))
        w = w.iloc[-80:]
        if len(w) == 0:
            return None
        fig = go.Figure()
        fig.add_trace(_candles(w))
        for col, color in ((f"EMA{cc.WEEKLY_EMA}", cc.MA_COLORS["EMA10"]),
                           (f"SMA{cc.WEEKLY_SMA}", cc.MA_COLORS["SMA200"])):
            if col in w.columns:
                fig.add_trace(go.Scatter(x=w.index, y=w[col], mode="lines",
                                         line=dict(color=color, width=1.2),
                                         name=col, showlegend=False,
                                         hovertemplate=f"{col} %{{y:.2f}}<extra></extra>"))
        layout = _base_layout(height=290)
        fig.update_layout(**layout)
        return fig
    except Exception:  # noqa: BLE001
        return None
