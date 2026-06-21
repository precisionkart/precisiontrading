"""dashboard_ui.py — redesigned Dashboard render layer (Phase 11 UI refresh).

A self-contained "trading terminal as modern SaaS" visual system for the
Dashboard view only. Everything is namespaced `ppx-` and the CSS is injected by
this module (`inject_css`), so it overlays the base style.css WITHOUT touching
it and cannot affect any other page.

Signature idea: *everything is a meter.* RS, Score, sector strength and
position progress all share one track-and-fill grammar with consistent colour
grading (green strong / amber ok / red weak), which makes RS & Score the loudest
thing on every row.

Render functions here REUSE the existing logic in common.py (store access,
_position_live, the exit dialog, star button, the inline detail expander) so the
behaviour is identical to before — only the presentation changes.
"""
from __future__ import annotations

import html as _html

import streamlit as st

import common as c
import dashboard_logic as dl
from pinpoint import ohlcv as ohlcv_mod
from pinpoint import store


# ---------------------------------------------------------------------------
# Design system (namespaced ppx-) — injected once per dashboard render.
# ---------------------------------------------------------------------------
_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500;600;700&display=swap');
:root{
  --ppx-canvas:#FAFAFC; --ppx-surface:#FFFFFF; --ppx-ink:#0B1020; --ppx-text:#474D5E;
  --ppx-muted:#969CAC; --ppx-line:#ECEDF2; --ppx-line2:#E2E4EC; --ppx-soft:#F1F2F6;
  --ppx-brand:#4F46E5; --ppx-brand-soft:#EEF0FE;
  --ppx-bull:#16A06A; --ppx-bull-soft:#E7F6EF; --ppx-bear:#E5484D; --ppx-bear-soft:#FCECEC;
  --ppx-warn:#D9870B; --ppx-warn-soft:#FBF1DE;
  --ppx-disp:'Space Grotesk','Inter',sans-serif; --ppx-mono:'JetBrains Mono',ui-monospace,monospace;
}
/* grade colours shared by every meter / number */
.ppx-g-strong{--g:#0E9C63}.ppx-g-good{--g:#2E9E57}.ppx-g-ok{--g:#C77D08}.ppx-g-weak{--g:#D83A3F}
.ppx-meter{height:6px;background:var(--ppx-line);border-radius:99px;overflow:hidden;display:block}
.ppx-meter>span{display:block;height:100%;border-radius:99px;background:var(--g,var(--ppx-bull))}
.ppx-meter.sm{height:4px}
.ppx-up{color:var(--ppx-bull)!important}.ppx-dn{color:var(--ppx-bear)!important}.ppx-flat{color:var(--ppx-muted)!important}

/* section headers */
.ppx-h{font-family:var(--ppx-disp);font-weight:600;font-size:17px;color:var(--ppx-ink);letter-spacing:-.01em;margin:34px 0 2px}
.ppx-sub{font-family:var(--ppx-mono);font-size:12px;color:var(--ppx-muted);margin:0 0 14px}

/* setups table (mockup .tbl) */
.ppx-tbl{width:100%;border-collapse:collapse}
.ppx-tbl thead th{font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--ppx-muted);font-weight:600;text-align:left;padding:10px 11px;border-bottom:1px solid var(--ppx-line)}
.ppx-tbl thead th.num{text-align:right}
.ppx-tbl tbody td{padding:12px 11px;border-bottom:1px solid var(--ppx-line);vertical-align:middle}
.ppx-tbl tbody tr:last-child td{border-bottom:0}
.ppx-tbl tbody tr.lead-row{background:var(--ppx-brand-soft)}
.ppx-tbl .cell-tkr{display:flex;align-items:center;gap:11px}
.ppx-tbl .cell-tkr .star{color:#D6D9E2;flex:none;font-size:14px}
.ppx-tbl .cell-tkr .star.on{color:var(--ppx-brand)}
.ppx-tbl .cell-tkr b{font-family:var(--ppx-disp);font-weight:700;font-size:15px;color:var(--ppx-ink);display:block}
.ppx-tbl .cell-tkr small{display:block;color:var(--ppx-muted);font-size:11.5px;font-weight:500;margin-top:1px}
.ppx-tbl .gnum{font-family:var(--ppx-mono);font-weight:700;font-size:14px;color:var(--g,var(--ppx-ink));display:flex;flex-direction:column;align-items:flex-end;gap:4px}
.ppx-tbl .gnum .ppx-meter{width:46px}
.ppx-tbl .td-num{text-align:right}
.ppx-tbl .pat{font-size:13px;color:var(--ppx-text)}
.ppx-tbl .sect{font-size:12px;color:var(--ppx-muted);font-weight:500}
.ppx-tbl .rr{font-family:var(--ppx-mono);font-weight:700;color:var(--ppx-ink);text-align:right}
.ppx-tbl .lvl{font-family:var(--ppx-mono);font-weight:600;font-size:13px}
.ppx-tbl .lvl-entry{color:var(--ppx-bull)}.ppx-tbl .lvl-stop{color:var(--ppx-bear)}.ppx-tbl .lvl-tgt{color:var(--ppx-ink)}
/* setups rebuilt as real Streamlit rows: thead-style header strip + row dividers */
.ppx-thr{display:grid;grid-template-columns:2.2fr 1fr 1fr 1.2fr 1.2fr 1.2fr 1fr 1.3fr;gap:.5rem;align-items:center;padding:9px 6px 7px;border-bottom:1px solid var(--ppx-line)}
.ppx-thr>span{font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--ppx-muted);font-weight:600}
.ppx-thr>span.num{text-align:right}
[class*="st-key-setups_row"]{border-bottom:1px solid var(--ppx-line);padding:2px 0}
[class*="st-key-setups_row_0"]{background:var(--ppx-brand-soft);border-radius:8px}

/* generic card */
.ppx-card{background:var(--ppx-surface);border:1px solid var(--ppx-line);border-radius:14px;box-shadow:0 1px 2px rgba(16,24,40,.04);overflow:hidden}
.ppx-card-h{display:flex;align-items:center;gap:9px;padding:14px 16px;border-bottom:1px solid var(--ppx-line)}
.ppx-card-h h3{margin:0;font-family:var(--ppx-disp);font-weight:600;font-size:14.5px;color:var(--ppx-ink)}
.ppx-card-h .s{font-size:12px;color:var(--ppx-muted)}
.ppx-card-h .right{margin-left:auto;display:flex;align-items:center;gap:9px}
.ppx-chip{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;padding:3px 10px;border-radius:99px}
.ppx-chip .dot{width:7px;height:7px;border-radius:99px}
.ppx-chip.bull{background:var(--ppx-bull-soft);color:#0F8B5C}.ppx-chip.bull .dot{background:#22C77E}
.ppx-chip.bear{background:var(--ppx-bear-soft);color:var(--ppx-bear)}.ppx-chip.bear .dot{background:var(--ppx-bear)}

/* KPI strip */
.ppx-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:13px;margin:6px 0 22px}
.ppx-kpi{background:var(--ppx-surface);border:1px solid var(--ppx-line);border-radius:12px;padding:14px 15px;box-shadow:0 1px 2px rgba(16,24,40,.04)}
.ppx-kpi .lab{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--ppx-muted);font-weight:600}
.ppx-kpi .big{font-family:var(--ppx-disp);font-size:24px;font-weight:600;color:var(--ppx-ink);margin-top:7px;display:flex;align-items:baseline;gap:7px;letter-spacing:-.01em}
.ppx-kpi .big .u{font-family:var(--ppx-mono);font-size:13px;color:var(--ppx-muted);font-weight:500}
.ppx-kpi .sub{margin-top:6px;font-size:12px;font-family:var(--ppx-mono)}

/* grade chips (RS / SCORE) — the signature */
.ppx-grades{display:flex;gap:9px}
.ppx-grade{background:var(--ppx-soft);border:1px solid var(--ppx-line2);border-radius:11px;padding:8px 11px;min-width:70px}
.ppx-grade i{font-style:normal;font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:var(--ppx-muted);font-weight:700;display:block}
.ppx-grade b{font-family:var(--ppx-mono);font-weight:700;font-size:23px;line-height:1.05;color:var(--g,var(--ppx-ink));display:block;margin:2px 0 6px}
.ppx-grade.ppx-g-strong,.ppx-grade.ppx-g-good{background:var(--ppx-bull-soft);border-color:#CBEBD9}
.ppx-grade.ppx-g-ok{background:var(--ppx-warn-soft);border-color:#F0DDB4}
.ppx-grade.ppx-g-weak{background:var(--ppx-bear-soft);border-color:#F3CFD0}

/* sector ladder */
.ppx-ladder{padding:6px 16px 12px}
.ppx-rung{display:grid;grid-template-columns:18px 116px 1fr 50px;gap:11px;align-items:center;padding:7px 0}
.ppx-rung+.ppx-rung{border-top:1px solid var(--ppx-line)}
.ppx-rung .rk{font-family:var(--ppx-mono);font-size:12px;color:var(--ppx-muted);font-weight:600;text-align:center}
.ppx-rung .nm{font-weight:600;color:var(--ppx-ink);font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ppx-rung .v{font-family:var(--ppx-mono);font-weight:700;font-size:13.5px;color:var(--g,var(--ppx-ink));text-align:right;display:flex;align-items:center;justify-content:flex-end;gap:5px}
.ppx-rung .v .tr{font-size:10px}
.ppx-rung.lead{background:linear-gradient(90deg,rgba(79,70,229,.05),transparent);border-radius:8px;margin:0 -6px;padding-left:6px;padding-right:6px}
.ppx-rung.lead .rk{color:var(--ppx-brand)}
.ppx-leadflag{font-size:9px;font-weight:700;letter-spacing:.05em;color:var(--ppx-brand);background:var(--ppx-brand-soft);border-radius:5px;padding:1px 6px;margin-left:7px;vertical-align:middle}

/* positions widget */
.ppx-pos-head{display:flex;align-items:center;gap:9px;padding:2px 2px 12px}
.ppx-pos-head h3{margin:0;font-family:var(--ppx-disp);font-weight:600;font-size:15px;color:var(--ppx-ink)}
.ppx-pos-head .s{font-size:12px;color:var(--ppx-muted)}
.ppx-pos-head .right{margin-left:auto}
.ppx-pos{display:grid;grid-template-columns:1fr auto;gap:3px 12px;padding:11px 0;align-items:center;border-top:1px solid var(--ppx-line)}
.ppx-pos .who{display:flex;align-items:baseline;gap:8px}
.ppx-pos .who b{font-family:var(--ppx-disp);font-weight:700;font-size:15px;color:var(--ppx-ink)}
.ppx-pos .who .e{font-family:var(--ppx-mono);font-size:11px;color:var(--ppx-muted)}
.ppx-pos .r{font-family:var(--ppx-mono);font-weight:700;font-size:16px;text-align:right}
.ppx-pos .status{grid-column:1;display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:600}
.ppx-pos .status .dot{width:7px;height:7px;border-radius:99px}
.ppx-st-hold{color:var(--ppx-muted)}.ppx-st-hold .dot{background:#B7BCC9}
.ppx-st-trim{color:#0F8B5C}.ppx-st-trim .dot{background:#22C77E}
.ppx-st-stop{color:var(--ppx-bear)}.ppx-st-stop .dot{background:var(--ppx-bear)}
.ppx-st-ema{color:#B5730A}.ppx-st-ema .dot{background:var(--ppx-warn)}
.ppx-pos .track{grid-column:2;width:118px}
.ppx-pos .track .cap{font-family:var(--ppx-mono);font-size:10px;color:var(--ppx-muted);display:flex;justify-content:space-between;margin-bottom:3px}
.ppx-pos-foot{display:flex;align-items:center;justify-content:space-between;padding:12px 2px 2px;margin-top:4px;border-top:1px solid var(--ppx-line)}
.ppx-pos-foot .lab{font-size:12px;color:var(--ppx-muted)}
.ppx-pos-foot b{font-family:var(--ppx-mono);font-weight:700;font-size:16px}
.ppx-pos-empty{padding:14px 2px;color:var(--ppx-muted);font-size:13px}

/* setup card body (lives inside a bordered st.container) */
.ppx-card-body{padding:2px 2px 4px}
.ppx-cb-top{display:flex;align-items:flex-start;gap:14px}
.ppx-cb-id{min-width:0}
.ppx-eyebrow{display:inline-flex;align-items:center;gap:5px;font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--ppx-brand);background:var(--ppx-brand-soft);padding:3px 8px;border-radius:99px;margin-bottom:7px}
.ppx-tk{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.ppx-tk .t{font-family:var(--ppx-disp);font-weight:700;font-size:24px;color:var(--ppx-ink);letter-spacing:-.02em}
.ppx-tk .px{font-family:var(--ppx-mono);font-size:16px;font-weight:600;color:var(--ppx-ink)}
.ppx-tk .chg{font-family:var(--ppx-mono);font-size:13px;font-weight:600}
.ppx-tk .spark{margin-left:2px;display:inline-flex}
.ppx-meta{margin-top:6px;color:var(--ppx-muted);font-size:12.5px}
.ppx-meta b{color:var(--ppx-text);font-weight:600}
.ppx-cb-grades{margin-left:auto;flex:none}
.ppx-levels{display:grid;grid-template-columns:repeat(4,1fr);margin-top:13px;border:1px solid var(--ppx-line);border-radius:10px;overflow:hidden}
.ppx-lvl{padding:9px 12px;border-right:1px solid var(--ppx-line)}
.ppx-lvl:last-child{border-right:0}
.ppx-lvl i{font-style:normal;font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--ppx-muted);font-weight:600;display:block}
.ppx-lvl b{font-family:var(--ppx-mono);font-weight:700;font-size:16px;color:var(--ppx-ink);display:block;margin-top:3px}
.ppx-lvl b.bull{color:var(--ppx-bull)}.ppx-lvl b.bear{color:var(--ppx-bear)}
.ppx-ef{font-size:10px;font-weight:700;color:#B5730A;background:var(--ppx-warn-soft);border-radius:6px;padding:2px 7px;margin-left:6px}
.ppx-lvl .sub{font-family:var(--ppx-mono);font-size:10px;color:var(--ppx-muted);margin-top:2px}

/* focus card middle band — chart, ATR caption, signal tags, checklist */
.ppx-chart{padding:14px 2px 2px}
.ppx-chart svg{width:100%;height:84px;display:block}
.ppx-chart .cap{font-family:var(--ppx-mono);font-size:11px;color:var(--ppx-muted);margin-top:6px}
.ppx-signals{display:flex;flex-wrap:wrap;gap:7px;padding:10px 2px 2px}
.ppx-tag{font-size:11.5px;font-weight:600;padding:4px 10px;border-radius:7px;display:inline-flex;align-items:center;gap:6px}
.ppx-tag .dot{width:6px;height:6px;border-radius:99px}
.ppx-tag.bull{background:var(--ppx-bull-soft);color:#0F8B5C}.ppx-tag.bull .dot{background:#22C77E}
.ppx-tag.warn{background:var(--ppx-warn-soft);color:#B5730A}.ppx-tag.warn .dot{background:var(--ppx-warn)}
.ppx-check{display:flex;align-items:center;gap:13px;flex-wrap:wrap;padding:12px 2px 2px}
.ppx-check .score{display:flex;align-items:center;gap:9px;min-width:180px;flex:1}
.ppx-check .score .ppx-meter{flex:1}
.ppx-check .score .lab{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ppx-muted);font-weight:600}
.ppx-check .score b{font-family:var(--ppx-mono);font-weight:700;color:var(--ppx-ink);font-size:14px}
.ppx-miss{display:flex;gap:7px;flex-wrap:wrap}
.ppx-miss .x{font-size:11.5px;font-weight:600;color:#C0383C;background:var(--ppx-bear-soft);border-radius:7px;padding:4px 9px;display:inline-flex;align-items:center;gap:5px}

/* round the bordered st.containers used for setup cards + positions (best-effort) */
[class*="st-key-ppxcard"] div[data-testid="stVerticalBlockBorderWrapper"],
[class*="st-key-ppxpos"] div[data-testid="stVerticalBlockBorderWrapper"]{border-radius:14px!important}
[class*="st-key-ppxcard"]{margin-bottom:11px}
"""


def inject_css() -> None:
    """Inject the ppx- design system. Call once at the top of the dashboard view."""
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Small helpers.
# ---------------------------------------------------------------------------
def _grade(v) -> str:
    """grade class for a 0-100 value: strong >=90 / good >=75 / ok >=60 / weak."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "ppx-g-ok"
    if f != f:
        return "ppx-g-ok"
    return ("ppx-g-strong" if f >= 90 else "ppx-g-good" if f >= 75
            else "ppx-g-ok" if f >= 60 else "ppx-g-weak")


def _pct(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f:
        return 0.0
    return max(2.0, min(100.0, f))


def _grade_chip(label: str, value) -> str:
    """A big RS/Score grade chip with a fill meter. The signature element."""
    g = _grade(value)
    txt = f"{float(value):.0f}" if isinstance(value, (int, float)) and value == value else "—"
    w = _pct(value)
    return (f"<div class='ppx-grade {g}'><i>{_html.escape(label)}</i><b>{txt}</b>"
            f"<span class='ppx-meter'><span style='width:{w:.0f}%'></span></span></div>")


def _money(x) -> str:
    return f"${float(x):,.2f}" if isinstance(x, (int, float)) and x == x else "—"


# ---------------------------------------------------------------------------
# KPI strip.
# ---------------------------------------------------------------------------
def kpi_strip(regime_state: str, n_setups: int, n_open: int, open_r) -> None:
    """Four-card KPI strip: regime · setups found · open positions · open R."""
    st_l = str(regime_state or "neutral").lower()
    if st_l in ("bull", "neutral-bull"):
        reg_word, reg_chip, reg_dot, reg_note = "Bull", "bull", "#22C77E", "Breadth strong"
    elif st_l in ("bear", "very-bear"):
        reg_word, reg_chip, reg_dot, reg_note = "Bear", "bear", "#E5484D", "Risk-off"
    else:
        reg_word, reg_chip, reg_dot, reg_note = regime_state.title(), "", "#B7BCC9", "Mixed signals"

    reg_chip_html = (f"<span class='ppx-chip {reg_chip}'><span class='dot'></span>{reg_note}</span>"
                     if reg_chip else
                     f"<span class='ppx-kpi .sub ppx-flat'>{_html.escape(reg_note)}</span>")

    try:
        r = float(open_r)
    except (TypeError, ValueError):
        r = 0.0
    r_cls = "ppx-up" if r > 0 else "ppx-dn" if r < 0 else "ppx-flat"
    r_txt = f"{'+' if r >= 0 else ''}{r:.1f}"
    pos_sub = ("all flat" if n_open == 0 else
               f"{'+' if r >= 0 else ''}{r:.1f}R open")

    st.markdown(
        "<div class='ppx-kpis'>"
        f"<div class='ppx-kpi'><div class='lab'>Market regime</div>"
        f"<div class='big'>{_html.escape(reg_word)}</div>{reg_chip_html}</div>"
        f"<div class='ppx-kpi'><div class='lab'>Setups found</div>"
        f"<div class='big'>{n_setups} <span class='u'>today</span></div>"
        f"<div class='sub ppx-flat'>scored 50+</div></div>"
        f"<div class='ppx-kpi'><div class='lab'>Open positions</div>"
        f"<div class='big'>{n_open} <span class='u'>live</span></div>"
        f"<div class='sub ppx-flat'>{_html.escape(pos_sub)}</div></div>"
        f"<div class='ppx-kpi'><div class='lab'>Open R</div>"
        f"<div class='big {r_cls}'>{r_txt}<span class='u'>R</span></div>"
        f"<div class='sub ppx-flat'>across open trades</div></div>"
        "</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Sector strength — ranked ladder (replaces the pill row / treemap).
# ---------------------------------------------------------------------------
def sector_ladder(themes: list) -> None:
    """Ranked sector-strength ladder: strongest at top, leader flagged, weak
    groups red at the bottom. `themes` is the scan's theme list — dicts with
    `theme`/`name`, `score`, `rank`."""
    rows = sorted((themes or []), key=lambda x: (x.get("rank") or 999))[:12]
    if not rows:
        st.markdown(
            "<div class='ppx-card'><div class='ppx-card-h'><h3>Sector strength</h3></div>"
            "<div class='ppx-ladder'><div class='ppx-sub' style='margin:6px 0'>"
            "Sector rankings unavailable for this scan.</div></div></div>",
            unsafe_allow_html=True)
        return

    rungs = []
    for i, t in enumerate(rows):
        name = str(t.get("theme") or t.get("name") or "—")
        score = t.get("score")
        g = _grade(score)
        w = _pct(score)
        sc_txt = f"{float(score):.0f}" if isinstance(score, (int, float)) and score == score else "—"
        if isinstance(score, (int, float)) and score == score:
            tr = ("<span class='tr ppx-up'>▲</span>" if score >= 60
                  else "<span class='tr ppx-flat'>▬</span>" if score >= 50
                  else "<span class='tr ppx-dn'>▼</span>")
        else:
            tr = ""
        lead = " lead" if i == 0 else ""
        flag = "<span class='ppx-leadflag'>LEADER</span>" if i == 0 else ""
        rungs.append(
            f"<div class='ppx-rung{lead} {g}'><div class='rk'>{i + 1}</div>"
            f"<div class='nm'>{_html.escape(name)}{flag}</div>"
            f"<span class='ppx-meter'><span style='width:{w:.0f}%'></span></span>"
            f"<div class='v'>{sc_txt} {tr}</div></div>")

    st.markdown(
        "<div class='ppx-card'><div class='ppx-card-h'><h3>Sector strength</h3>"
        f"<div class='right'><span class='s'>{len(rows)} groups · daily</span></div></div>"
        f"<div class='ppx-ladder'>{''.join(rungs)}</div></div>",
        unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Today's setups — ranked rows of real Streamlit widgets (clickable ticker/star).
# ---------------------------------------------------------------------------
def setups_table(rows: list, on_pick, n_total: int = None, key: str = "setups") -> None:
    """Ranked setups as REAL Streamlit rows (so the ticker and star are
    clickable): Ticker · RS · Score · Entry · Stop · Target · R:R · Sector, with
    RS/Score graded meters. `rows` is a list of dicts (Ticker, RS, Score, Sector,
    Entry, Stop, R:R). Clicking a ticker calls `on_pick(ticker)`; clicking a star
    adds it to the watchlist. Pattern column removed."""
    if not rows:
        st.markdown("<div class='ppx-card'><div class='ppx-card-h'><h3>Today's setups</h3></div>"
                    "<div style='padding:14px 16px;color:var(--ppx-muted);font-size:13px'>"
                    "No names scored 50+.</div></div>", unsafe_allow_html=True)
        return

    sub = "ranked by score" if n_total is None else f"ranked by score · {n_total} scanned"
    watched = set(store.load_watchlist()) if hasattr(store, "load_watchlist") else set()

    def _gnum(val) -> str:
        """RS/Score cell: graded number + little meter, right-aligned. Reuses the
        table-scoped .gnum / .ppx-meter styling by wrapping in a .ppx-tbl div."""
        g, w = _grade(val), _pct(val)
        txt = f"{float(val):.0f}" if isinstance(val, (int, float)) and val == val else "—"
        return (f"<div class='ppx-tbl' style='text-align:right'><div class='gnum {g}'>{txt}"
                f"<span class='ppx-meter sm'><span style='width:{w:.0f}%'></span></span></div></div>")

    def _lvl(txt: str, cls: str) -> str:
        return f"<div class='ppx-tbl' style='text-align:right'><span class='lvl {cls}'>{txt}</span></div>"

    with st.container(border=True, key="ppxcard_setups"):
        st.markdown(
            "<div class='ppx-card-h' style='padding:14px 6px'><h3>Today's setups</h3>"
            f"<span class='s'>{_html.escape(sub)}</span></div>"
            "<div class='ppx-thr'><span>Ticker</span><span class='num'>RS</span>"
            "<span class='num'>Score</span><span class='num'>Entry</span>"
            "<span class='num'>Stop</span><span class='num'>Target</span>"
            "<span class='num'>R : R</span><span>Sector</span></div>",
            unsafe_allow_html=True)

        for i, r in enumerate(rows):
            tk = str(r.get("Ticker", ""))
            rs, sc, rr_ = r.get("RS"), r.get("Score"), r.get("R:R")
            sect = str(r.get("Sector") or "—")
            e_, s_ = r.get("Entry"), r.get("Stop")
            rr_txt = f"{rr_:.1f}:1" if isinstance(rr_, (int, float)) and rr_ == rr_ else "—"
            e_txt, s_txt = _money(e_), _money(s_)
            # Target column = the 3R objective (Entry + 3*risk), matching the focus
            # card headline. (R:R is left as-is — still measured-move-based.)
            if (isinstance(e_, (int, float)) and e_ == e_ and isinstance(s_, (int, float))
                    and s_ == s_ and (e_ - s_) > 0):
                t_txt = _money(e_ + 3 * (e_ - s_))
            else:
                t_txt = "—"

            with st.container(key=f"{key}_row_{i}"):
                cols = st.columns([2.2, 1, 1, 1.2, 1.2, 1.2, 1, 1.3],
                                  vertical_alignment="center")
                with cols[0]:
                    star_col, tk_col = st.columns([1, 4], vertical_alignment="center")
                    with star_col:
                        if st.button("★", key=f"{key}_star_{i}_{tk}",
                                     type="primary" if tk in watched else "tertiary",
                                     help="On watchlist" if tk in watched else "Add to watchlist"):
                            store.add_to_watchlist(tk)
                            st.toast(f"{tk} added to watchlist")
                            st.rerun()
                    with tk_col:
                        if st.button(tk, key=f"{key}_tk_{i}_{tk}", help=f"Analyse {tk}"):
                            on_pick(tk)
                cols[1].markdown(_gnum(rs), unsafe_allow_html=True)
                cols[2].markdown(_gnum(sc), unsafe_allow_html=True)
                cols[3].markdown(_lvl(e_txt, "lvl-entry"), unsafe_allow_html=True)
                cols[4].markdown(_lvl(s_txt, "lvl-stop"), unsafe_allow_html=True)
                cols[5].markdown(_lvl(t_txt, "lvl-tgt"), unsafe_allow_html=True)
                cols[6].markdown(
                    f"<div class='ppx-tbl' style='text-align:right'><span class='rr'>{rr_txt}</span></div>",
                    unsafe_allow_html=True)
                cols[7].markdown(
                    f"<div class='ppx-tbl'><span class='sect'>{_html.escape(sect)}</span></div>",
                    unsafe_allow_html=True)
def positions_widget(lives: list | None = None) -> None:
    """Compact OPEN POSITIONS widget: ticker · current R · status pill ·
    entry→target progress meter, with total open R in the footer. Reuses the
    existing _position_live enrichment and the exit dialog."""
    toast = st.session_state.pop("_pos_toast", None)
    if toast:
        st.toast(toast, icon="✅")
    if lives is None:
        lives = [c._position_live(p) for p in store.open_positions()]

    open_r = sum(lv["r_mult"] for lv in lives if lv.get("r_mult") is not None)
    rcls = "ppx-up" if open_r > 0 else "ppx-dn" if open_r < 0 else "ppx-flat"
    head_chip = (f"<span class='ppx-chip {'bull' if open_r >= 0 else 'bear'}'>"
                 f"<span class='dot'></span>{'+' if open_r >= 0 else ''}{open_r:.1f}R</span>")

    with st.container(border=True, key="ppxpos_box"):
        st.markdown(
            "<div class='ppx-pos-head'><h3>Open positions</h3>"
            f"<span class='s'>{len(lives)} live</span>"
            f"<div class='right'>{head_chip if lives else ''}</div></div>",
            unsafe_allow_html=True)

        if not lives:
            st.markdown("<div class='ppx-pos-empty'>No open positions yet — setups you "
                        "trade will show here with live R and exit signals.</div>",
                        unsafe_allow_html=True)
        for i, lv in enumerate(lives):
            tk = str(lv.get("ticker", ""))
            cp, ema10, stop, entry = lv.get("price"), lv.get("ema10"), lv.get("stop"), lv.get("entry")
            r = lv.get("r_mult")
            tgt = lv.get("target_3r") or lv.get("target_5r")

            if r is None:
                rtxt, r_c = "—", "ppx-flat"
            elif r >= 0:
                rtxt, r_c = f"+{r:.1f}R", "ppx-up"
            else:
                rtxt, r_c = f"{r:.1f}R", "ppx-dn"

            below = (cp is not None and ema10 == ema10 and ema10 is not None and cp < ema10)
            near_stop = (cp is not None and stop and stop > 0 and cp <= stop * 1.02)
            if near_stop:
                s_cls, s_txt = "ppx-st-stop", "Near stop"
            elif r is not None and r >= 3:
                s_cls, s_txt = "ppx-st-trim", "Trim zone"
            elif below:
                s_cls, s_txt = "ppx-st-ema", "Below 10 EMA"
            else:
                s_cls, s_txt = "ppx-st-hold", "Holding"

            # progress entry -> first target (clamped); grade by how far along.
            if entry and tgt and tgt > entry and cp is not None:
                prog = max(0.0, min(100.0, (cp - entry) / (tgt - entry) * 100.0))
            elif cp is not None and entry and cp < entry:
                prog = 0.0
            else:
                prog = 0.0
            pg = ("ppx-g-strong" if prog >= 66 else "ppx-g-good" if prog >= 33
                  else "ppx-g-ok" if prog > 0 else "ppx-g-weak")

            rowcol, xcol = st.columns([15, 1], vertical_alignment="center")
            rowcol.markdown(
                f"<div class='ppx-pos {pg}'>"
                f"<div class='who'><b>{_html.escape(tk)}</b><span class='e'>in {_money(entry)}</span></div>"
                f"<div class='r {r_c}'>{rtxt}</div>"
                f"<span class='status {s_cls}'><span class='dot'></span>{s_txt}</span>"
                f"<div class='track'><div class='cap'><span>entry</span><span>target</span></div>"
                f"<span class='ppx-meter sm'><span style='width:{prog:.0f}%'></span></span></div>"
                f"</div>", unsafe_allow_html=True)
            if xcol.button("✕", key=f"ppxclose_{i}_{tk}", help=f"Close {tk}"):
                c._exit_dialog(lv)

        if lives:
            st.markdown(
                f"<div class='ppx-pos-foot'><span class='lab'>Total open R</span>"
                f"<b class='{rcls}'>{'+' if open_r >= 0 else ''}{open_r:.1f}R</b></div>",
                unsafe_allow_html=True)

        # + Add position (reuses common's inline form + store.add_position)
        if st.session_state.get("add_pos_open"):
            c._add_position_form()
        else:
            if st.button("＋ Add position", key="ppx_add_pos_btn"):
                st.session_state["add_pos_open"] = True
                st.rerun()


# ---------------------------------------------------------------------------
# Setup card — compact, RS/Score-forward, with the inline detail expander.
# ---------------------------------------------------------------------------
def _focus_chart_svg(closes: list, entry, stop) -> str:
    """Mockup-style area chart from real closes, with dashed entry (green) and
    stop (red) guide lines placed at their true price levels. viewBox 560x88."""
    pts = [c for c in (closes or []) if isinstance(c, (int, float)) and c == c]
    pts = pts[-80:]
    if len(pts) < 2:
        return ""
    W, H, pad = 560.0, 88.0, 8.0
    lo, hi = min(pts), max(pts)
    # include entry/stop in the vertical range so their guide lines sit correctly
    extra = [v for v in (entry, stop) if isinstance(v, (int, float)) and v == v]
    lo = min([lo] + extra)
    hi = max([hi] + extra)
    rng = (hi - lo) or 1.0

    def y(v):
        return pad + (hi - v) / rng * (H - 2 * pad)

    def x(i):
        return i / (len(pts) - 1) * W

    line = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(pts))
    area = f"M0,{H:.0f} L" + " L".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(pts)) + f" L{W:.0f},{H:.0f} Z"
    guides = ""
    if isinstance(entry, (int, float)) and entry == entry:
        guides += (f"<line x1='0' y1='{y(entry):.1f}' x2='{W:.0f}' y2='{y(entry):.1f}' "
                   "stroke='#16A06A' stroke-width='1' stroke-dasharray='4 4' opacity='0.55'/>")
    if isinstance(stop, (int, float)) and stop == stop:
        guides += (f"<line x1='0' y1='{y(stop):.1f}' x2='{W:.0f}' y2='{y(stop):.1f}' "
                   "stroke='#E5484D' stroke-width='1' stroke-dasharray='4 4' opacity='0.5'/>")
    lx, ly = x(len(pts) - 1), y(pts[-1])
    return (
        "<div class='ppx-chart'><svg viewBox='0 0 560 88' preserveAspectRatio='none' fill='none'>"
        "<defs><linearGradient id='ppxfill' x1='0' y1='0' x2='0' y2='1'>"
        "<stop offset='0' stop-color='#4F46E5' stop-opacity='0.12'/>"
        "<stop offset='1' stop-color='#4F46E5' stop-opacity='0'/></linearGradient></defs>"
        f"{guides}"
        f"<path d='{area}' fill='url(#ppxfill)'/>"
        f"<polyline points='{line}' stroke='#4F46E5' stroke-width='2' "
        "stroke-linecap='round' stroke-linejoin='round'/>"
        f"<circle cx='{lx:.1f}' cy='{ly:.1f}' r='3.5' fill='#4F46E5' stroke='#fff' stroke-width='1.5'/>"
        "</svg></div>")


def _focus_band(pr, closes: list, entry, stop) -> str:
    """The focus card's middle band — chart + ATR caption + signal tags +
    checklist — all from the real analyzer result `pr`. Degrades gracefully:
    chart always renders from closes; the rest appears once `pr` is available."""
    chart = _focus_chart_svg(closes, entry, stop)

    cap = ""
    tags = ""
    check = ""
    if pr is not None:
        atr = getattr(pr, "atr_14", None)
        if isinstance(atr, (int, float)) and atr == atr:
            def _a(k):
                v = getattr(pr, k, None)
                return f"{abs(v):.2f}" if isinstance(v, (int, float)) and v == v else "—"
            cs = getattr(pr, "compression_score", None)
            cs_txt = (f" &nbsp;·&nbsp; coil {cs:.0f}/25"
                      if isinstance(cs, (int, float)) and cs == cs else "")
            cap = (f"<div class='cap'>ATR(14) ${atr:.2f} &nbsp;·&nbsp; "
                   f"5–10 {_a('spread_5_10_atr')} ATR &nbsp;·&nbsp; "
                   f"10–20 {_a('spread_10_20_atr')} ATR &nbsp;·&nbsp; "
                   f"price-to-20 {_a('spread_price_20_atr')} ATR{cs_txt}</div>")

        def _items(v):
            try:
                return [str(x) for x in list(v) if str(x)]
            except (TypeError, ValueError):
                return []
        flags = _items(getattr(pr, "flags", None))
        warns = _items(getattr(pr, "warnings", None))
        pills = ([f"<span class='ppx-tag bull'><span class='dot'></span>{_html.escape(x)}</span>"
                  for x in flags[:4]]
                 + [f"<span class='ppx-tag warn'><span class='dot'></span>{_html.escape(x)}</span>"
                    for x in warns[:4]])
        if pills:
            tags = f"<div class='ppx-signals'>{''.join(pills)}</div>"

        crit = getattr(pr, "criteria", None) or []
        if crit:
            total = len(crit)
            passed = sum(1 for c_ in crit if getattr(c_, "passed", False))
            pctw = passed / total * 100.0 if total else 0.0
            g = ("ppx-g-strong" if pctw >= 90 else "ppx-g-good" if pctw >= 75
                 else "ppx-g-ok" if pctw >= 60 else "ppx-g-weak")
            misses = []
            for c_ in crit:
                if not getattr(c_, "passed", False):
                    lab = _html.escape(str(getattr(c_, "label", "")))
                    val = getattr(c_, "value", "")
                    vtxt = f" · {_html.escape(str(val))}" if val else ""
                    misses.append(f"<span class='x'>✕ {lab}{vtxt}</span>")
            miss_html = f"<div class='ppx-miss'>{''.join(misses)}</div>" if misses else ""
            check = (f"<div class='ppx-check'><div class='score {g}'>"
                     f"<span class='lab'>Checklist</span>"
                     f"<span class='ppx-meter'><span style='width:{pctw:.0f}%'></span></span>"
                     f"<b>{passed}/{total}</b></div>{miss_html}</div>")

    return chart + cap_wrap(cap) + tags + check


def cap_wrap(cap: str) -> str:
    # the ATR caption lives inside the chart block in the mockup; if we have a
    # caption but the chart already closed, wrap it so spacing matches.
    return f"<div class='ppx-chart' style='padding-top:0'>{cap}</div>" if cap else ""


def _card_body(row: dict, spark: str, price: float, chg, ef: bool, focus: bool,
               pr=None, closes: list | None = None) -> str:
    tk = _html.escape(str(row.get("Ticker", "")))
    pat = _html.escape(str(row.get("Pattern") or "—"))
    sect = _html.escape(str(row.get("Sector") or ""))
    px = f"${price:,.2f}" if price == price else "—"
    if chg is not None and chg == chg:
        chg_html = (f"<span class='chg {'ppx-up' if chg >= 0 else 'ppx-dn'}'>"
                    f"{'▲' if chg >= 0 else '▼'} {abs(chg):.1f}%</span>")
    else:
        chg_html = ""

    e, s_, rr_ = row.get("Entry"), row.get("Stop"), row.get("R:R")
    if isinstance(e, (int, float)) and e == e and isinstance(s_, (int, float)) and s_ == s_:
        risk = e - s_
        rr_txt = f"{rr_:.1f}:1" if isinstance(rr_, (int, float)) and rr_ == rr_ else "—"
        # Headline target = the 3R objective (Entry + 3*risk); the full 1.0x
        # measured move is demoted to a clearly-labelled stretch line below.
        t3_txt = f"${e + 3 * risk:,.2f}" if risk > 0 else "—"
        mm_txt = (f"${e + rr_ * risk:,.2f}"
                  if isinstance(rr_, (int, float)) and rr_ == rr_ and risk > 0 else None)
        risk_pct = f"{risk / e * 100:.1f}% risk" if e else "risk"
        stretch = (f"<div class='ppx-meta' style='margin-top:7px'>"
                   f"Measured move (stretch): <b>{mm_txt}</b></div>" if mm_txt else "")
        levels = (
            "<div class='ppx-levels'>"
            f"<div class='ppx-lvl'><i>Entry</i><b class='bull'>${e:,.2f}</b>"
            "<div class='sub'>breakout trigger</div></div>"
            f"<div class='ppx-lvl'><i>Stop</i><b class='bear'>${s_:,.2f}</b>"
            f"<div class='sub'>{risk_pct}</div></div>"
            f"<div class='ppx-lvl'><i>Target</i><b>{t3_txt}</b>"
            "<div class='sub'>3R target</div></div>"
            f"<div class='ppx-lvl'><i>R : R</i><b>{rr_txt}</b>"
            "<div class='sub'>reward / risk</div></div>"
            "</div>"
            f"{stretch}")
    else:
        levels = ""

    # focus-only middle band: chart + ATR caption + signal tags + checklist
    band = ""
    if focus:
        band = _focus_band(pr, closes or [], c._num_or_none(e), c._num_or_none(s_))

    eyebrow = "<span class='ppx-eyebrow'>★ Today's focus</span>" if focus else ""
    ef_badge = "<span class='ppx-ef'>Earnings</span>" if ef else ""
    return (
        "<div class='ppx-card-body'>"
        "<div class='ppx-cb-top'><div class='ppx-cb-id'>"
        f"{eyebrow}"
        f"<div class='ppx-tk'><span class='t'>{tk}</span>"
        f"<span class='px'>{px}</span>{chg_html}<span class='spark'>{spark}</span>{ef_badge}</div>"
        f"<div class='ppx-meta'><b>{pat}</b>{(' · ' + sect) if sect else ''}</div>"
        "</div>"
        f"<div class='ppx-cb-grades ppx-grades'>{_grade_chip('RS', row.get('RS'))}"
        f"{_grade_chip('Score', row.get('Score'))}</div>"
        "</div>"
        f"{levels}"
        f"{band}"
        "</div>")


def setup_card(row: dict, detail_fn, key: str, tier: int = None,
               ef: bool = False, focus: bool = False) -> None:
    """Compact setup card: ticker/price/spark + RS & Score grade chips + the
    entry/stop/target/R:R levels, with a 📋 Trade button (tier 1/2), ★ watchlist
    toggle, and the inline expand to the full detail view (chart + checklist).
    Drop-in replacement for common.compact_card — same behaviour, new look."""
    tk = str(row.get("Ticker"))
    open_set = st.session_state.setdefault("open_cards", set())
    is_open = tk in open_set

    daily = ohlcv_mod.fetch_daily(tk, cache_only=c.cloud_mode()).df
    closes = list(daily["Close"]) if daily is not None and len(daily) else []
    spark = dl.sparkline_svg(closes)
    price = float(closes[-1]) if closes else float("nan")
    chg = ((closes[-1] / closes[-2] - 1.0) * 100.0) if len(closes) >= 2 else None

    entry, stop = c._num_or_none(row.get("Entry")), c._num_or_none(row.get("Stop"))

    # Focus card: render chart-first, then fill the rich band (ATR/tags/checklist)
    # on the next beat. We fetch the analyzer detail only once per ticker and
    # cache it in session, so the page never blocks on first paint.
    pr = None
    if focus:
        warmed = st.session_state.setdefault("_focus_detail", {})
        if tk in warmed:
            pr = warmed[tk]
        else:
            # paint now without detail; warm it and rerun so the band fills in.
            st.session_state["_focus_warm_pending"] = tk

    with st.container(border=True, key=f"ppxcard_t{tier or 0}_{key}"):
        st.markdown(_card_body(row, spark, price, chg, ef=ef, focus=focus,
                               pr=pr, closes=closes),
                    unsafe_allow_html=True)
        st.markdown("<hr class='pp-divider'/>", unsafe_allow_html=True)
        if focus:
            # mockup footer: Trade setup / Chart, docked under the card.
            f0, f1, f3 = st.columns([4, 2.4, 1], vertical_alignment="center")
            with f0:
                if entry and stop and st.button("📈 Trade setup", key=f"ppxtrade_focus_{key}",
                                                type="primary", use_container_width=True):
                    st.session_state["active_trade"] = c._build_active_trade(row, price)
                    st.switch_page("views/position_sizer.py")
            with f1:
                st.link_button("📊 Chart", c.tradingview_url(tk), use_container_width=True)
            with f3:
                c.star_button(tk, key=f"ppxrow_{key}")
        else:
            f0, f1, f2 = st.columns([6, 1, 1], vertical_alignment="center")
            with f0:
                if tier in (1, 2) and entry and stop:
                    if st.button("📋 Trade", key=f"ppxtrade{tier}_{key}",
                                 type="primary" if tier == 1 else "secondary"):
                        st.session_state["active_trade"] = c._build_active_trade(row, price)
                        st.switch_page("views/position_sizer.py")
            with f1:
                c.star_button(tk, key=f"ppxrow_{key}")
            with f2:
                if st.button("∨" if not is_open else "∧", key=f"ppxexp_{key}", help="Details"):
                    (open_set.discard if is_open else open_set.add)(tk)
                    st.rerun()
            if is_open:
                pr_open = detail_fn(tk)
                if pr_open is not None:
                    c.render_detail_inline(pr_open)

    # Deferred warm-up for the focus card: the card has now painted (chart-first);
    # fetch the analyzer detail once, cache it, and rerun so the ATR/tags/checklist
    # band fills in on the next beat without blocking first paint.
    if focus and st.session_state.get("_focus_warm_pending") == tk:
        st.session_state.pop("_focus_warm_pending", None)
        try:
            warmed = st.session_state.setdefault("_focus_detail", {})
            warmed[tk] = detail_fn(tk)
            st.rerun()
        except Exception:  # noqa: BLE001 — never let warm-up break the page
            st.session_state.setdefault("_focus_detail", {})[tk] = None