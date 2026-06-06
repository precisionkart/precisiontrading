"""charts_plotly.py — interactive dashboard charts (Phase 7.6).

Fluoro palette (pinpoint.chart_config FLUORO_*), thin MAs, faint volume, range
slider, and Entry/.89-Stop/Target labels as PILLS anchored to the right edge at
their y-price levels (Plotly annotations with bgcolor + borderpad). The dashed
price lines stay (1px); the pills carry the labels. The report's mplfinance path
is unaffected — it uses the muted palette.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from pinpoint import chart_config as cc
from pinpoint import ohlcv as ohlcv_mod

_MA_WIDTH = {"EMA5": 1.0, "EMA10": 1.0, "EMA20": 1.0, "SMA50": 1.0, "SMA200": 1.5}
_DISPLAY_BARS = 160
_F = cc.MA_COLORS_FLUORO


def _candles(d: pd.DataFrame) -> go.Candlestick:
    return go.Candlestick(
        x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"],
        name="Price", increasing_line_color=cc.FLUORO_UP, decreasing_line_color=cc.FLUORO_DOWN,
        increasing_fillcolor=cc.FLUORO_UP, decreasing_fillcolor=cc.FLUORO_DOWN,
        line_width=1, whiskerwidth=0.4, showlegend=False)


def _ma_traces(d: pd.DataFrame, cols) -> list:
    traces = []
    for col in cols:
        if col in d.columns and d[col].notna().any():
            traces.append(go.Scatter(
                x=d.index, y=d[col], mode="lines", name=col,
                line=dict(color=_F[col], width=_MA_WIDTH.get(col, 1.0)),
                hovertemplate=f"{col} %{{y:.2f}}<extra></extra>", showlegend=False))
    return traces


def _layout(height: int, right_margin: int = 56) -> dict:
    return dict(
        height=height, plot_bgcolor=cc.WHITE, paper_bgcolor=cc.WHITE,
        margin=dict(l=8, r=right_margin, t=6, b=6), dragmode="zoom",
        font=dict(family="Geist, Inter, system-ui, sans-serif", color=cc.TEXT),
        hovermode="x unified",
        xaxis=dict(showgrid=False, color=cc.AXIS, tickfont=dict(size=10),
                   rangeslider=dict(visible=False)),
        yaxis=dict(side="right", showgrid=True, gridcolor=cc.GRID, nticks=6,
                   color=cc.AXIS, tickfont=dict(size=10), tickprefix="$"),
    )


def _edge_pills(fig: go.Figure, d: pd.DataFrame, entry, stop, target, reward_risk) -> None:
    """Entry / Stop / Target pills anchored to the right edge at their y-prices,
    with a simple anti-overlap nudge so close levels stay readable."""
    levels = []
    if entry is not None and entry == entry:
        levels.append(("Entry", float(entry), cc.FLUORO_UP, "#FFFFFF"))
    if stop is not None and stop == stop:
        levels.append((".89 Stop", float(stop), cc.FLUORO_DOWN, "#FFFFFF"))
    if target is not None and target == target:
        rr = f" · {reward_risk:.1f}:1" if (reward_risk is not None and reward_risk == reward_risk) else ""
        levels.append((f"Target{rr}", float(target), "#1F2937", "#FFFFFF"))
    if not levels:
        return

    lo = float(d["Low"].iloc[-_DISPLAY_BARS:].min())
    hi = float(d["High"].iloc[-_DISPLAY_BARS:].max())
    rng = max(hi - lo, 1e-6)
    min_gap = rng * 0.045                     # min vertical spacing for labels

    # anti-overlap: place label y's at least min_gap apart (lines stay at true price)
    order = sorted(range(len(levels)), key=lambda i: levels[i][1])
    label_y = {}
    prev = None
    for i in order:
        y = levels[i][1]
        if prev is not None and y - prev < min_gap:
            y = prev + min_gap
        label_y[i] = y
        prev = y

    for i, (label, price, bg, fg) in enumerate(levels):
        fig.add_hline(y=price, line_dash="dash", line_color=bg, line_width=1, opacity=0.7)
        fig.add_annotation(
            xref="paper", x=1.0, xanchor="left", yref="y", y=label_y[i], yanchor="middle",
            text=f"{label} ${price:,.2f}", showarrow=False,
            bgcolor=bg, bordercolor=bg, borderpad=4, borderwidth=0,
            font=dict(color=fg, size=11, family="Geist Mono, monospace"))


def daily_figure(daily: pd.DataFrame, entry: Optional[float] = None,
                 stop: Optional[float] = None, target: Optional[float] = None,
                 pattern_bars: int = 0, reward_risk: Optional[float] = None) -> Optional[go.Figure]:
    """Interactive daily chart with right-edge price pills."""
    try:
        d = ohlcv_mod.add_moving_averages(daily).iloc[-_DISPLAY_BARS:]
        if len(d) == 0:
            return None
        fig = go.Figure()
        fig.add_trace(_candles(d))
        for tr in _ma_traces(d, ("EMA5", "EMA10", "EMA20", "SMA50", "SMA200")):
            fig.add_trace(tr)

        vmax = float(d["Volume"].max()) if "Volume" in d.columns else 0.0
        if vmax > 0:
            colors = np.where(d["Close"] >= d["Open"], cc.FLUORO_UP, cc.FLUORO_DOWN)
            fig.add_trace(go.Bar(x=d.index, y=d["Volume"], marker_color=colors,
                                 marker_line_width=0, opacity=0.18, yaxis="y2",
                                 name="Vol", hovertemplate="Vol %{y:,.0f}<extra></extra>",
                                 showlegend=False))

        layout = _layout(height=420, right_margin=118)   # room for the pills
        layout["yaxis2"] = dict(overlaying="y", side="left", showgrid=False,
                                range=[0, vmax * 4.0 if vmax else 1], visible=False)
        layout["xaxis"]["rangeslider"] = dict(visible=True, thickness=0.06)
        fig.update_layout(**layout)

        if pattern_bars and pattern_bars > 1 and len(d) > pattern_bars:
            fig.add_vrect(x0=d.index[-pattern_bars], x1=d.index[-1],
                          fillcolor=cc.FLUORO_UP, opacity=0.05, line_width=0)

        _edge_pills(fig, d, entry, stop, target, reward_risk)
        return fig
    except Exception:  # noqa: BLE001
        return None


def sector_treemap(rows: list) -> Optional[go.Figure]:
    """Sector heatmap: area = rank weight, color = RS score (fluoro green→red),
    hover shows RS / Δ-vs-prior / hot / top-3 names. `rows` from
    dashboard_logic.treemap_data."""
    if not rows:
        return None
    try:
        labels = [r["label"] for r in rows]
        values = [r["value"] for r in rows]
        scores = [r["score"] if r["score"] is not None else 0.0 for r in rows]
        smax = max(abs(s) for s in scores) or 1.0
        # text on each block: sector + RS score
        text = [f"<b>{r['label']}</b><br>RS {r['score']:.0f}" if r["score"] is not None
                else f"<b>{r['label']}</b>" for r in rows]
        customdata = [[("● Hot" if r["hot"] else ""),
                       (f"{r['delta']:+.1f}" if r["delta"] is not None else "—"),
                       r["top3"]] for r in rows]
        fig = go.Figure(go.Treemap(
            labels=labels, parents=[""] * len(labels), values=values,
            text=text, textinfo="text", textposition="middle center",
            textfont=dict(family="Geist, sans-serif", size=15, color="#FFFFFF"),
            insidetextfont=dict(family="Geist, sans-serif", size=15, color="#FFFFFF"),
            marker=dict(colors=scores, colorscale=[[0, cc.FLUORO_DOWN], [0.5, "#1F2937"],
                                                   [1, cc.FLUORO_UP]],
                        cmin=-smax, cmax=smax, cornerradius=8,
                        line=dict(width=0)),          # no inter-block border lines
            root=dict(color="rgba(0,0,0,0)"),         # transparent root (no header strip)
            pathbar=dict(visible=False),
            customdata=customdata,
            hovertemplate=("<b>%{label}</b><br>RS %{color:.1f}<br>"
                           "Δ vs prior: %{customdata[1]}  %{customdata[0]}<br>"
                           "Top: %{customdata[2]}<extra></extra>"),
            tiling=dict(pad=8), sort=True, branchvalues="total"))
        fig.update_layout(height=300, margin=dict(l=2, r=2, t=2, b=2),
                          paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          font=dict(family="Geist, sans-serif", color="#FFFFFF", size=13))
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
        for col, color in ((f"EMA{cc.WEEKLY_EMA}", _F["EMA10"]),
                           (f"SMA{cc.WEEKLY_SMA}", _F["SMA200"])):
            if col in w.columns:
                fig.add_trace(go.Scatter(x=w.index, y=w[col], mode="lines",
                                         line=dict(color=color, width=1.2),
                                         name=col, showlegend=False,
                                         hovertemplate=f"{col} %{{y:.2f}}<extra></extra>"))
        fig.update_layout(**_layout(height=260))
        return fig
    except Exception:  # noqa: BLE001
        return None
