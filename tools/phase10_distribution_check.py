"""tools/phase10_distribution_check.py — observe-only empirical check after the
first live --publish on the rescaled engine (Phase 10 follow-up).

Reads the latest real scan (published latest_scan.json if present, else the local
scan cache) and writes two distributions to data/phase10_distribution_check.txt:

  1. Score distribution across the 80/65/50 tier bands — are Focus names spread
     sensibly or bunched in one zone?
  2. ATR Tight-Coil firing — how many Focus names hit compression_score >= 22/25
     (is the band reasonable or too strict)?

NO TUNING — this only observes. Both are config-only adjustments if the numbers
look off. Run after a live scan:  python tools/phase10_distribution_check.py
"""

import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pinpoint import store
from pinpoint.config import CONFIG


def _load_focus():
    pub = store.load_published_scan()
    if pub and pub.get("lists", {}).get("focus"):
        return pd.DataFrame(pub["lists"]["focus"]), "published latest_scan.json"
    cache = store.load_scan_cache()
    if cache is not None and len(cache.lists.get("focus", [])):
        return cache.lists["focus"], "local scan cache"
    return pd.DataFrame(), "(no scan found)"


def main():
    focus, src = _load_focus()
    out = os.path.join(CONFIG.paths.data_dir, "phase10_distribution_check.txt")
    lines = [f"Phase 10 distribution check — source: {src}", f"Focus names: {len(focus)}", ""]

    if len(focus) and "pinpoint_score" in focus.columns:
        s = pd.to_numeric(focus["pinpoint_score"], errors="coerce").dropna()
        elite = int((s >= 80).sum()); good = int(((s >= 65) & (s < 80)).sum())
        watch = int(((s >= 50) & (s < 65)).sum()); below = int((s < 50).sum())
        lines += ["== Score distribution (tier bands) =="]
        lines += [f"  Elite   (80-100): {elite}",
                  f"  Good    (65-79):  {good}",
                  f"  Watch   (50-64):  {watch}",
                  f"  Below 50:         {below}",
                  f"  min/median/max:   {s.min():.1f} / {s.median():.1f} / {s.max():.1f}",
                  f"  VERDICT: {'spread across bands' if (elite and watch) or good else 'BUNCHED — review weighting'}",
                  ""]
    else:
        lines += ["== Score distribution == (no pinpoint_score column)", ""]

    if len(focus) and "compression_score" in focus.columns:
        cs = pd.to_numeric(focus["compression_score"], errors="coerce").dropna()
        coil = int((cs >= 22).sum()); coiled = int(((cs >= 15) & (cs < 22)).sum())
        loose = int((cs <= 7).sum())
        lines += ["== ATR compression (Tight Coil >= 22/25) =="]
        lines += [f"  Tight Coil (>=22): {coil} of {len(cs)}",
                  f"  Coiled (15-21):    {coiled}",
                  f"  Loose (<=7):       {loose}",
                  f"  min/median/max:    {cs.min():.1f} / {cs.median():.1f} / {cs.max():.1f}",
                  f"  VERDICT: {'fires on real names' if coil else 'TOO STRICT — no Tight Coils'}",
                  ""]
    else:
        lines += ["== ATR compression == (no compression_score column — needs a live Focus run)", ""]

    os.makedirs(CONFIG.paths.data_dir, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
