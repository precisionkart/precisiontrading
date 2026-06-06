#!/usr/bin/env python3
"""scan.py — Pinpoint Trading Scanner CLI entry point.

A research/screening aid ONLY: it never trades, connects to a brokerage, or
moves money (see pinpoint.DISCLAIMER).

Phase 1 implements the deterministic, offline ``--selftest`` that runs the whole
pipeline on fixtures (no network) and prints all three watchlists. The live
flags (--targets/--focus/--earnings/...) are scaffolded and wired to real Finviz
in later phases.

Usage examples:
    python scan.py --selftest
    python scan.py --targets            # (Phase 2: live Finviz)
    python scan.py --analyze AAPL,MSFT  # (Phase 7: My-Picks engine)
"""

from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

import os
from datetime import datetime

from pinpoint import DISCLAIMER, __version__
from pinpoint import sample_data
from pinpoint import regime as regime_mod
from pinpoint import pipeline
from pinpoint.config import CONFIG
from pinpoint.finviz_client import FinvizClient


# --------------------------------------------------------------------------
# Terminal rendering (rich if available, else plain pandas).
# --------------------------------------------------------------------------
def _print_table(title: str, df: pd.DataFrame, cols: list[str] | None = None) -> None:
    """Delegate to the rich terminal renderer (color-coded score, no borders)."""
    from pinpoint import output
    valid_cols = [c for c in cols if c in df.columns] if (cols and df is not None) else cols
    output.print_table(title, df, valid_cols)


def _fmt(v) -> str:
    if isinstance(v, float):
        if v != v:
            return "-"
        return f"{v:,.2f}"
    return str(v)


# --------------------------------------------------------------------------
# Selftest (offline, deterministic).
# --------------------------------------------------------------------------
def run_selftest() -> int:
    print(f"Pinpoint Scanner v{__version__} — OFFLINE SELFTEST (no network)")
    print("-" * 72)
    print(DISCLAIMER)
    print("-" * 72)

    # 1. Regime from fixture (Section 3.2).
    reg = regime_mod.from_fundaments(sample_data.regime_fixture())
    print("\n" + reg.banner())

    # 2. Targets (Section 3.3-3.5 / 3.9).
    raw_universe = sample_data.universe_df()
    universe = pipeline.normalize_universe(raw_universe)
    targets = pipeline.build_targets(universe, reg, save_snapshot=False)
    _print_table(
        "TARGETS — leaders past every gate, ranked by pinpoint_score",
        targets,
        cols=["ticker", "sector", "price", "rs", "rel_volume", "pct_below_high",
              "stage", "growth", "n_layers", "pinpoint_score"],
    )
    if "rs_label" in targets.attrs:
        print(f"  RS column = {targets.attrs['rs_label']}")
    if len(targets):
        print("  Top name's layer breakdown: " + str(targets.iloc[0]["layers"]))

    # 3. Focus (Section 7) — OHLCV-enriched: valid pattern + R:R>=5:1 only.
    index_daily = sample_data.ohlcv_fixture("SPY", shape="index")
    focus = pipeline.enrich_focus(
        targets, universe, reg,
        ohlcv_provider=lambda tk: sample_data.ohlcv_fixture(tk),
        index_daily=index_daily,
    )
    _print_table(
        "FOCUS — valid pattern + R:R>=5:1 only (OHLCV-enriched)",
        focus,
        cols=["ticker", "sector", "price", "rs", "pattern", "entry_trigger",
              "stop", "stop_kind", "risk", "reward_risk", "continuity",
              "n_layers", "pinpoint_score"],
    )

    # 4. Earnings (Section 7) — gap-UP only.
    earnings = pipeline.build_earnings(sample_data.earnings_df(), persist=False)
    _print_table(
        "EARNINGS — gapped-UP reactions only (reaction > numbers), by RS",
        earnings,
        cols=["ticker", "sector", "price", "gap", "rel_volume", "rs", "setup"],
    )

    # 5. Assertions so --selftest is a real smoke test (Section 9).
    ok = True
    problems: list[str] = []
    if len(targets) == 0:
        ok = False
        problems.append("Targets list is empty")
    if len(focus) == 0:
        ok = False
        problems.append("Focus list is empty")
    if len(earnings) == 0:
        ok = False
        problems.append("Earnings list is empty")
    # gap-down name must be excluded
    if "BADER" in set(earnings.get("ticker", pd.Series(dtype="object")).tolist()):
        ok = False
        problems.append("gap-DOWN name leaked into Earnings list")
    # cheap name must be excluded from earnings
    if "MEHV" in set(earnings.get("ticker", pd.Series(dtype="object")).tolist()):
        ok = False
        problems.append("sub-$10 name leaked into Earnings list")
    # failing-gate names must not be in Targets
    bad_in_targets = set(targets.get("ticker", pd.Series(dtype="object")).tolist()) & {
        "LOWV", "FARLO", "CHEAP", "DULLV"}
    if bad_in_targets:
        ok = False
        problems.append(f"gate-failing names leaked into Targets: {sorted(bad_in_targets)}")

    print("\n" + "-" * 72)
    if ok:
        print("✅ SELFTEST PASSED — all three lists built; gates and gap-up rule enforced.")
        return 0
    print("❌ SELFTEST FAILED:")
    for p in problems:
        print(f"   - {p}")
    return 1


# --------------------------------------------------------------------------
# Live flags (Phase 2: Targets / Earnings / Focus wired to Finviz).
# --------------------------------------------------------------------------
def _not_yet(flag: str) -> int:
    print(f"[{flag}] live path is implemented in a later phase. "
          f"Run `python scan.py --selftest` for the offline end-to-end demo.")
    return 0


def _print_warnings(warnings: list[str]) -> None:
    if warnings:
        print("\n⚠️  Data warnings:")
        for w in warnings:
            print(f"   - {w}")


def run_live(args) -> int:
    """Live Finviz scan for --targets / --earnings / --focus / --all."""
    print(f"Pinpoint Scanner v{__version__} — LIVE scan")
    print("-" * 72)
    print(DISCLAIMER)
    print("-" * 72)

    client = FinvizClient()

    # Regime first (Section 3.2) — every list is read in this context.
    reg = regime_mod.fetch_regime(client)
    print("\n" + reg.banner())

    want_report = getattr(args, "report", False)
    want_ipo = args.all or args.ipo or want_report
    want_targets = args.all or args.targets or want_report
    want_focus = args.all or args.focus or want_report
    want_earnings = args.all or args.earnings or want_report

    any_403 = False
    targets = focus = earnings_df = None
    ipo_watch = None

    if args.min_growth:
        print("  (--min-growth: applying hard EPS/Sales QoQ >= 25% Finviz filters to Targets)")
    if args.ignore_rvol:
        print("  (--ignore-rvol: dropping the relative-volume gate for off-hours prep)")

    # Theme/industry context (Section 3.10) — needed for Targets/Focus scoring.
    theme_ctx = None
    if want_targets or want_focus:
        from pinpoint import themes
        theme_ctx = themes.build_theme_context(client)
        _print_theme_rankings(theme_ctx)
        themes.log_theme_history(theme_ctx)          # weekly drift instrumentation

    # IPO watchlist (Section 3.11) — also yields the ipo_edge context.
    ipo_ctx = {}
    if want_ipo:
        from pinpoint import ipo as ipo_mod
        ipo_res = ipo_mod.build_ipo_watchlist(client)
        ipo_watch = ipo_res.watchlist
        ipo_ctx = ipo_res.ipo_ctx
        _print_table("IPO WATCHLIST — recent IPOs vs their initial high (3.11)",
                     ipo_watch,
                     cols=["ticker", "sector", "price", "ipo_high", "pct_from_high",
                           "ipo_date", "status"])
        _print_warnings(ipo_res.warnings)

    if want_targets or want_focus:
        # Fetch the universe ONCE and reuse it for both lists.
        uni = pipeline.fetch_targets_universe(client, reg, min_growth=args.min_growth,
                                              theme_ctx=theme_ctx, ipo_ctx=ipo_ctx,
                                              ignore_rvol=args.ignore_rvol)
        any_403 = any_403 or _has_block(uni.warnings)
        targets = uni.df
        if want_targets:
            shown = targets.head(args.limit) if args.limit else targets
            _print_table(
                "TARGETS — leaders past every gate, ranked by pinpoint_score",
                shown,
                cols=["ticker", "sector", "theme", "price", "rs", "pct_below_high",
                      "stage", "n_layers", "pinpoint_score"],
            )
            if "rs_label" in targets.attrs:
                print(f"  RS column = {targets.attrs['rs_label']}")
            _print_warnings(uni.warnings)
        if want_focus:
            index_daily = pipeline.ohlcv_mod.fetch_daily(
                pipeline.CONFIG.regime.benchmarks[0]).df
            focus = pipeline.enrich_focus(targets, uni.universe, reg,
                                          index_daily=index_daily, limit=args.limit,
                                          theme_ctx=theme_ctx, ipo_ctx=ipo_ctx)
            _print_table(
                "FOCUS — valid pattern + R:R>=5:1 only, ranked by pinpoint_score",
                focus,
                cols=["ticker", "sector", "theme", "price", "rs", "pattern",
                      "entry_trigger", "stop", "reward_risk", "continuity",
                      "n_layers", "pinpoint_score"],
            )
            if len(focus) == 0:
                print("  (no names currently show a valid pattern with R:R>=5:1)")

    if want_earnings:
        ern = pipeline.run_earnings(client, limit=args.limit)
        earnings_df = ern.df
        _print_table(
            "EARNINGS — gapped-UP reactions only (reaction > numbers), by RS",
            ern.df,
            cols=["ticker", "sector", "price", "gap", "rel_volume", "rs", "setup"],
        )
        _print_warnings(ern.warnings)
        any_403 = any_403 or _has_block(ern.warnings)

    if want_report:
        date = datetime.now().strftime("%Y-%m-%d")
        as_of = datetime.now().strftime("%H:%M")
        render_outputs(focus, targets, earnings_df, reg, date, as_of, ipo=ipo_watch)

    if any_403:
        print("\n🚫 Finviz returned 403/blocked on at least one request.")
        print("   Try raising CONFIG.network.request_delay_s to 2-3s, use a "
              "residential IP, or a Finviz Elite session (see README).")

    _print_final_status(targets, focus, earnings_df, ipo_watch, any_403)
    return 0


def _n(df) -> int:
    return 0 if df is None else len(df)


def _print_final_status(targets, focus, earnings, ipo, any_403: bool) -> None:
    """One-line run summary (or honest partial-success line)."""
    n_ipo_near = 0
    if ipo is not None and len(ipo) and "status" in ipo.columns:
        n_ipo_near = int((ipo["status"] != "below IPO high").sum())
    summary = (f"{_n(focus)} Focus, {_n(targets)} Targets, "
               f"{_n(earnings)} Earnings, {n_ipo_near} IPOs near high")
    if any_403:
        now = datetime.now().strftime("%H:%M")
        print(f"\n⚠️  Partial: Finviz throttled at {now} — results may be incomplete "
              f"({summary}).")
    else:
        print(f"\n✅ Run complete: {summary}.")


def _has_block(warnings: list[str]) -> bool:
    return any("403" in w or "block" in w.lower() for w in warnings)


def publish(cache_top: int = 250) -> int:
    """Run a full scan and write data/latest_scan.json + the universe snapshot +
    a pre-cached OHLCV set, for the read-only cloud app (Phase 8). One broad
    Finviz screen (all views) feeds both the gated Targets and the My-Picks RS
    reference, so this is screen-efficient."""
    from datetime import datetime, timezone
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
    except Exception:  # noqa: BLE001
        et = timezone.utc
    from pinpoint import themes as themes_mod, ipo as ipo_mod, store
    from pinpoint.config import RS_REFERENCE_SCREEN
    from pinpoint.rs_rating import compute_rs

    print(f"Pinpoint Scanner v{__version__} — PUBLISH (writing latest_scan.json)")
    client = FinvizClient()
    warnings: list[str] = []

    reg = regime_mod.fetch_regime(client)
    print(reg.banner())
    theme_ctx = themes_mod.build_theme_context(client)
    themes_mod.log_theme_history(theme_ctx)
    warnings += theme_ctx.warnings
    ipo_res = ipo_mod.build_ipo_watchlist(client)
    warnings += ipo_res.warnings

    # One broad screen (all views) -> universe; Targets via in-code gates.
    res = client.fetch_universe(RS_REFERENCE_SCREEN)
    warnings += res.warnings
    universe = pipeline.normalize_universe(res.df)
    targets = pipeline.build_targets(universe, reg, raw_df=res.df, theme_ctx=theme_ctx,
                                     ipo_ctx=ipo_res.ipo_ctx)
    index_daily = pipeline.ohlcv_mod.fetch_daily(CONFIG.regime.benchmarks[0]).df
    focus = pipeline.enrich_focus(targets, universe, reg, index_daily=index_daily,
                                  theme_ctx=theme_ctx, ipo_ctx=ipo_res.ipo_ctx)
    ern = pipeline.run_earnings(client)
    warnings += ern.warnings

    # Snapshot (full normalized fields) for My-Picks RS + offline grading.
    store.save_universe_snapshot(universe)

    # Pre-cache OHLCV: scan names + top-N broad by RS (so cloud My Picks works).
    names = set()
    for df in (targets, focus, ern.df, ipo_res.watchlist):
        if df is not None and "ticker" in getattr(df, "columns", []):
            names |= set(df["ticker"].dropna().tolist())
    if cache_top > 0 and len(universe):
        u = universe.copy()
        u["rs"] = compute_rs(u[[c for c in ("perf_week", "perf_month", "perf_quarter",
                                            "perf_half", "perf_year") if c in u.columns]])
        names |= set(u.nlargest(cache_top, "rs")["ticker"].dropna().tolist())
    names |= set(CONFIG.regime.benchmarks)
    print(f"pre-caching OHLCV for {len(names)} tickers...")
    cached = 0
    for t in sorted(names):
        if not pipeline.ohlcv_mod.fetch_daily(t).empty:
            cached += 1

    now_utc = datetime.now(timezone.utc)
    as_of_et = now_utc.astimezone(et).strftime("%H:%M %Z")
    path = store.save_published_scan(
        reg, theme_ctx.theme_rank,
        {"focus": focus, "targets": targets, "earnings": ern.df, "ipo": ipo_res.watchlist},
        as_of_et=as_of_et, as_of_utc=now_utc.isoformat())

    blocked = _has_block(warnings)
    print(f"\n{'⚠️ Partial' if blocked else '✅'} published: "
          f"{_n(focus)} Focus, {_n(targets)} Targets, {_n(ern.df)} Earnings, "
          f"{_n(ipo_res.watchlist)} IPO; OHLCV cached {cached}/{len(names)}; "
          f"snapshot {len(universe)} rows -> {path}")
    return 1 if (blocked and _n(targets) == 0) else 0


def _print_theme_rankings(theme_ctx) -> None:
    if theme_ctx is None or not theme_ctx.theme_rank:
        return
    print("\n🔥 THEME RANKINGS (RS vs each other):")
    for theme, rec in theme_ctx.top_themes(len(theme_ctx.theme_rank)):
        hot = " ◄ hot" if rec["pct"] >= 0.70 else ""
        print(f"   #{rec['rank']:>2} {theme:<18} RS-score {rec['score']:>7.2f}{hot}")
    if theme_ctx.industry_rank:
        print("   Top 5 industry groups:")
        for ind, rec in theme_ctx.top_industries(5):
            print(f"     #{rec['rank']:>2} {ind}")


def render_outputs(focus, targets, earnings, regime, date: str, as_of: str,
                   ipo=None, demo: bool = False) -> str:
    """Render annotated charts for Focus, then the HTML report + CSV/XLSX."""
    from pinpoint import charts, report, output

    out_dir = CONFIG.paths.output_dir
    chart_dir = os.path.join(out_dir, "charts")
    chart_paths: dict[str, str] = {}
    if focus is not None and len(focus):
        for _, r in focus.iterrows():
            tk = r["ticker"]
            res = pipeline.ohlcv_mod.fetch_daily(tk)
            if res.empty:
                continue
            ann = charts.ChartAnnotation(
                entry=r.get("entry_trigger"), stop=r.get("stop"),
                target=r.get("measured_target"), reward_risk=r.get("reward_risk"),
                pattern_label=str(r.get("pattern", "")).split(" /")[0].split(" (")[0],
                pattern_bars=int(r.get("pattern_bars", 0) or 0),
                score=r.get("pinpoint_score"), rs=r.get("rs"))
            p = charts.render_focus_chart(tk, res.df, os.path.join(chart_dir, f"{tk}_{date}.png"), ann)
            if p:
                chart_paths[tk] = p

    report_path = os.path.join(out_dir, f"report_{date}.html")
    ipo_report = ipo.head(15) if ipo is not None and len(ipo) else ipo  # cap report table
    try:
        report.render_report(focus, targets, earnings, regime, report_path, as_of, date,
                             chart_paths=chart_paths, disclaimer=DISCLAIMER,
                             ipo=ipo_report, demo=demo)
        print(f"\n📄 Report: {report_path}")
    except Exception as exc:  # noqa: BLE001 — never let report rendering crash the run
        print(f"\n⚠️  HTML report failed to render ({exc}); lists were still printed "
              f"above and written to CSV/XLSX.")
        report_path = ""

    # CSV/XLSX are independent of the HTML render so they survive a chart failure.
    try:
        output.write_all_csv({"focus": focus, "targets": targets, "earnings": earnings,
                              "ipo": ipo}, out_dir, date)
        output.write_xlsx(focus, targets, earnings, os.path.join(out_dir, f"pinpoint_{date}.xlsx"))
        print(f"   CSV/XLSX written to {out_dir}/")
    except Exception as exc:  # noqa: BLE001
        print(f"   ⚠️  CSV/XLSX write failed: {exc}")
    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scan.py", description="Pinpoint Trading Scanner (research aid only).")
    parser.add_argument("--selftest", action="store_true",
                        help="run the offline deterministic pipeline on fixtures")
    parser.add_argument("--all", action="store_true", help="Targets + Focus + Earnings (live)")
    parser.add_argument("--targets", action="store_true", help="build Targets (live)")
    parser.add_argument("--focus", action="store_true", help="build Focus (live)")
    parser.add_argument("--earnings", action="store_true", help="build Earnings (live)")
    parser.add_argument("--ipo", action="store_true", help="IPO list (Phase 5)")
    parser.add_argument("--analyze", metavar="TICKERS", help="grade a comma-sep list (Phase 7)")
    parser.add_argument("--chart", metavar="TICKER", help="render one chart (Phase 4)")
    parser.add_argument("--limit", type=int, default=None, help="cap list sizes")
    parser.add_argument("--min-growth", action="store_true",
                        help="apply hard EPS/Sales QoQ >=25%% Finviz filters to Targets (3.4)")
    parser.add_argument("--report", action="store_true",
                        help="render charts + HTML report + CSV/XLSX to output/")
    parser.add_argument("--publish", action="store_true",
                        help="write data/latest_scan.json + snapshot + OHLCV cache (cloud)")
    parser.add_argument("--cache-top", type=int, default=250,
                        help="pre-cache OHLCV for the top-N broad names on --publish")
    parser.add_argument("--ignore-rvol", action="store_true",
                        help="drop the relative-volume gate (weekend/evening prep runs)")
    parser.add_argument("--no-csv", action="store_true", help="skip CSV output (Phase 4)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.selftest:
        return run_selftest()

    if args.publish:
        return publish(cache_top=args.cache_top)

    if args.analyze:
        return _not_yet("--analyze")
    if args.chart:
        return _not_yet("--chart")
    if args.all or args.targets or args.focus or args.earnings or args.report or args.ipo:
        return run_live(args)

    parser.print_help()
    print("\nTip: start with `python scan.py --selftest`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
