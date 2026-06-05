"""report.py — the HTML report (spec Section 7 + product brief).

Renders output/report_YYYY-MM-DD.html: an Inter-typeset page with a regime dot,
one card per Focus name (large ticker, sector/theme, score + layer chips, the
annotated chart, and a 4-column Entry/Stop/Target/R:R grid), then a collapsible
Targets table and an Earnings Reactions section. Charts are embedded as base64
data URIs so the file is fully self-contained (portable + screenshot-safe).
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

import pandas as pd
from jinja2 import Template

from . import __version__

logger = logging.getLogger("pinpoint.report")

_REGIME_DOT = {"bull": "#16A34A", "neutral": "#737373", "bear": "#DC2626"}


def _img_data_uri(path: Optional[str]) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _chips(layers_str: str) -> list[str]:
    if not isinstance(layers_str, str) or not layers_str:
        return []
    if layers_str.startswith("DISQUALIFIED"):
        return [layers_str]
    return [c.strip() for c in layers_str.split("|") if c.strip()]


_TEMPLATE = Template(r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pinpoint — {{ date }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root { --border:#E5E5E5; --gray:#737373; --ink:#0A0A0A; --green:#16A34A; --red:#DC2626; }
  * { box-sizing:border-box; }
  body { font-family:'Inter',system-ui,-apple-system,sans-serif; color:var(--ink);
         background:#FFFFFF; margin:0; padding:0 32px 80px; font-feature-settings:"tnum" 1; }
  .wrap { max-width:1080px; margin:0 auto; }
  header { display:flex; align-items:baseline; justify-content:space-between;
           padding:32px 0 8px; }
  header h1 { font-size:26px; font-weight:700; letter-spacing:-0.02em; margin:0; }
  header .asof { color:var(--gray); font-size:13px; }
  .regime { display:flex; align-items:center; gap:8px; font-size:14px; font-weight:500;
            margin:6px 0 28px; }
  .dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
  .disclaimer { color:var(--gray); font-size:11px; border-top:1px solid var(--border);
                margin-top:48px; padding-top:16px; }
  h2 { font-size:14px; font-weight:600; text-transform:uppercase; letter-spacing:0.06em;
       color:var(--gray); margin:40px 0 16px; }
  /* Focus cards */
  .card { border:1px solid var(--border); border-radius:14px; padding:22px 24px;
          margin-bottom:22px; box-shadow:0 1px 3px rgba(0,0,0,0.04); }
  .card-head { display:flex; align-items:baseline; justify-content:space-between; }
  .tk { font-size:30px; font-weight:700; letter-spacing:-0.03em; }
  .sub { color:var(--gray); font-size:13px; margin-left:10px; font-weight:500; }
  .score { font-size:15px; font-weight:600; }
  .score .rs { color:var(--gray); font-weight:500; margin-left:10px; }
  .chips { margin:12px 0 14px; }
  .chip { display:inline-block; border:1px solid var(--border); border-radius:999px;
          padding:3px 10px; font-size:11px; color:#374151; margin:0 6px 6px 0; }
  .chart img { width:100%; border:1px solid var(--border); border-radius:10px;
               box-shadow:0 1px 4px rgba(0,0,0,0.05); }
  .grid4 { display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:var(--border);
           border:1px solid var(--border); border-radius:10px; overflow:hidden; margin-top:16px; }
  .cell { background:#FFFFFF; padding:12px 16px; }
  .cell .k { color:var(--gray); font-size:11px; text-transform:uppercase; letter-spacing:0.05em; }
  .cell .v { font-size:18px; font-weight:600; margin-top:3px; }
  .v.green { color:var(--green); } .v.red { color:var(--red); }
  /* tables */
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:right; padding:8px 10px; border-bottom:1px solid var(--border); }
  th:first-child,td:first-child { text-align:left; }
  th { color:var(--gray); font-weight:600; font-size:11px; text-transform:uppercase;
       letter-spacing:0.05em; }
  td.tk { font-size:13px; font-weight:600; }
  details { border:1px solid var(--border); border-radius:12px; padding:6px 18px 14px; }
  summary { cursor:pointer; font-size:13px; font-weight:600; color:#374151; padding:10px 0; }
  .empty { color:var(--gray); font-size:13px; padding:8px 0; }
</style></head>
<body><div class="wrap">
  <header>
    <h1>Pinpoint <span style="color:var(--gray);font-weight:500">— {{ date }}</span></h1>
    <span class="asof">Data as of {{ as_of }}</span>
  </header>
  <div class="regime">
    <span class="dot" style="background:{{ regime_color }}"></span>
    {{ regime_word }} — <span style="color:var(--gray);font-weight:400">{{ regime_text }}</span>
    {% if demo %}<span style="margin-left:10px;border:1px solid var(--border);border-radius:999px;padding:2px 9px;font-size:11px;color:var(--gray)">demo: ticker-list grading</span>{% endif %}
  </div>

  <h2>Focus — valid pattern + R:R ≥ 5:1</h2>
  {% if focus|length == 0 %}<div class="empty">No names currently show a valid pattern with R:R ≥ 5:1.</div>{% endif %}
  {% for f in focus %}
  <div class="card">
    <div class="card-head">
      <div><span class="tk">{{ f.ticker }}</span><span class="sub">{{ f.sector }}{% if f.theme %} · {{ f.theme }}{% endif %} · {{ f.pattern }}</span></div>
      <div class="score">{{ f.score }}<span class="rs">RS {{ f.rs }}</span></div>
    </div>
    <div class="chips">{% for c in f.chips %}<span class="chip">{{ c }}</span>{% endfor %}</div>
    <div class="chart">{% if f.chart %}<img src="{{ f.chart }}" alt="{{ f.ticker }} chart">{% else %}<div class="empty">(chart unavailable)</div>{% endif %}</div>
    <div class="grid4">
      <div class="cell"><div class="k">Entry</div><div class="v green">${{ f.entry }}</div></div>
      <div class="cell"><div class="k">Stop (.89)</div><div class="v red">${{ f.stop }}</div></div>
      <div class="cell"><div class="k">Target</div><div class="v">${{ f.target }}</div></div>
      <div class="cell"><div class="k">R:R</div><div class="v">{{ f.rr }}:1</div></div>
    </div>
  </div>
  {% endfor %}

  <h2>Targets</h2>
  <details {% if focus|length == 0 %}open{% endif %}>
    <summary>{{ targets|length }} ranked leaders</summary>
    <table><thead><tr>{% for h in target_cols %}<th>{{ h }}</th>{% endfor %}</tr></thead>
    <tbody>{% for r in targets %}<tr>{% for h in target_cols %}<td class="{{ 'tk' if loop.first }}">{{ r[h] }}</td>{% endfor %}</tr>{% endfor %}</tbody></table>
  </details>

  {% if ipo and ipo|length %}
  <h2>IPO Watchlist — recent IPOs vs initial high</h2>
  <table><thead><tr>{% for h in ipo_cols %}<th>{{ h }}</th>{% endfor %}</tr></thead>
  <tbody>{% for r in ipo %}<tr>{% for h in ipo_cols %}<td class="{{ 'tk' if loop.first }}">{{ r[h] }}</td>{% endfor %}</tr>{% endfor %}</tbody></table>
  {% endif %}

  <h2>Earnings Reactions — gapped up only</h2>
  {% if earnings|length == 0 %}<div class="empty">No qualifying earnings reactions.</div>{% else %}
  <table><thead><tr>{% for h in earn_cols %}<th>{{ h }}</th>{% endfor %}</tr></thead>
  <tbody>{% for r in earnings %}<tr>{% for h in earn_cols %}<td class="{{ 'tk' if loop.first }}">{{ r[h] }}</td>{% endfor %}</tr>{% endfor %}</tbody></table>
  {% endif %}

  <div class="disclaimer">{{ disclaimer }} · Pinpoint Scanner v{{ version }}.
  RS is a labeled proxy (percentile of a front-weighted trailing return), NOT IBD's RS Rating.
  Time-frame continuity is approximated from weekly+daily (no free 65-min data).</div>
</div></body></html>""")


def _fmt(v, nd=2):
    if isinstance(v, float):
        if v != v:
            return "—"
        return f"{v:,.{nd}f}"
    return "—" if v is None else str(v)


def render_report(focus: pd.DataFrame, targets: pd.DataFrame, earnings: pd.DataFrame,
                  regime, out_path: str, as_of: str, date: str,
                  chart_paths: Optional[dict] = None,
                  disclaimer: str = "", ipo: Optional[pd.DataFrame] = None,
                  demo: bool = False) -> str:
    """Render the HTML report to `out_path` and return the path."""
    chart_paths = chart_paths or {}

    focus_records = []
    for _, r in (focus.iterrows() if focus is not None and len(focus) else []):
        tk = r["ticker"]
        focus_records.append({
            "ticker": tk,
            "sector": r.get("sector") or "",
            "theme": r.get("theme_rank") or r.get("theme") or "",
            "pattern": r.get("pattern") or "",
            "score": _fmt(r.get("pinpoint_score"), 1),
            "rs": _fmt(r.get("rs"), 0),
            "chips": _chips(r.get("layers", "")),
            "chart": _img_data_uri(chart_paths.get(tk)),
            "entry": _fmt(r.get("entry_trigger")),
            "stop": _fmt(r.get("stop")),
            "target": _fmt(r.get("measured_target")),
            "rr": _fmt(r.get("reward_risk"), 1),
        })

    target_cols = [c for c in ["ticker", "sector", "theme", "price", "rs", "stage", "growth",
                               "n_layers", "pinpoint_score"] if targets is not None and c in targets.columns]
    targets_records = []
    for _, r in (targets.iterrows() if targets is not None and len(targets) else []):
        targets_records.append({c: _fmt(r[c], 1 if c in ("pinpoint_score", "rs") else 2)
                                if isinstance(r[c], float) else r[c] for c in target_cols})

    earn_cols = [c for c in ["ticker", "sector", "price", "gap", "rel_volume", "rs"]
                 if earnings is not None and c in earnings.columns]
    earn_records = []
    for _, r in (earnings.iterrows() if earnings is not None and len(earnings) else []):
        earn_records.append({c: _fmt(r[c]) if isinstance(r[c], float) else r[c] for c in earn_cols})

    ipo_cols = [c for c in ["ticker", "sector", "price", "ipo_high", "pct_from_high",
                            "ipo_date", "status"] if ipo is not None and c in ipo.columns]
    ipo_records = []
    for _, r in (ipo.iterrows() if ipo is not None and len(ipo) else []):
        ipo_records.append({c: _fmt(r[c]) if isinstance(r[c], float) else r[c] for c in ipo_cols})

    state = getattr(regime, "state", "neutral")
    html = _TEMPLATE.render(
        date=date, as_of=as_of, version=__version__,
        regime_color=_REGIME_DOT.get(state, "#737373"),
        regime_word=state.upper(),
        regime_text="; ".join(getattr(regime, "rationale", []) or []),
        focus=focus_records,
        targets=targets_records, target_cols=target_cols,
        earnings=earn_records, earn_cols=earn_cols,
        ipo=ipo_records, ipo_cols=ipo_cols, demo=demo,
        disclaimer=disclaimer,
    )
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("wrote report %s", out_path)
    return out_path
