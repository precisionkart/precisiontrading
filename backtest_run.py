#!/usr/bin/env python3
"""Run the Pinpoint walk-forward backtest from the command line.

Usage:
  python backtest_run.py
  python backtest_run.py --start 2025-09-01 --end 2026-03-01
  python backtest_run.py --tickers NVDA,AMD,CRWD
  python backtest_run.py --account 50000 --risk 0.005
"""

import argparse
import logging

from pinpoint.backtest import BacktestEngine, BACKTEST_UNIVERSE


def main():
    parser = argparse.ArgumentParser(prog="backtest_run.py")
    parser.add_argument("--start", default="2025-12-01")
    parser.add_argument("--end", default="2026-03-01")
    parser.add_argument("--account", type=float, default=100_000)
    parser.add_argument("--risk", type=float, default=0.005)
    parser.add_argument("--tickers", default="")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    tickers = ([t.strip().upper() for t in args.tickers.split(",") if t.strip()]
               if args.tickers else BACKTEST_UNIVERSE)

    engine = BacktestEngine(start_date=args.start, end_date=args.end,
                            account_size=args.account, risk_pct=args.risk,
                            universe=tickers)

    print(f"""
╔══════════════════════════════════════════════════════╗
║         PINPOINT TRADING BACKTEST                     ║
╠══════════════════════════════════════════════════════╣
║  Period:     {args.start} → {args.end}
║  Universe:   {len(tickers)} tickers
║  Account:    ${args.account:,.0f}
║  Risk/trade: {args.risk*100:.1f}% (${args.account*args.risk:,.0f})
╚══════════════════════════════════════════════════════╝
    """)

    r = engine.run()
    bt = r["best_trade"] or {"ticker": "—", "r": 0.0, "pattern": "—"}
    wt = r["worst_trade"] or {"ticker": "—", "r": 0.0}

    print(f"""
╔══════════════════════════════════════════════════════╗
║              BACKTEST RESULTS                         ║
╠══════════════════════════════════════════════════════╣
║  Signals fired:    {r['total_signals']}
║  Trades taken:     {r['total_trades']}
║  Fill rate:        {r['fill_rate_pct']:.1f}%
╠══════════════════════════════════════════════════════╣
║  Win rate:         {r['win_rate_pct']:.1f}%
║  Avg R (all):      {r['avg_r_all']:.2f}R
║  Avg R (winners):  {r['avg_r_winners']:.2f}R
║  Avg R (losers):   {r['avg_r_losers']:.2f}R
║  Expectancy:       {r['expectancy']:.2f}R per trade
╠══════════════════════════════════════════════════════╣
║  Total P&L:        ${r['total_dollar_pnl']:,.0f}
║  Total return:     {r['total_return_pct']:.1f}%
║  Max drawdown:     {r['max_drawdown_pct']:.1f}%
║  Final equity:     ${r['final_equity']:,.0f}
╠══════════════════════════════════════════════════════╣
║  Best trade:  {bt['ticker']} {bt['r']:.1f}R ({bt['pattern']})
║  Worst trade: {wt['ticker']} {wt['r']:.1f}R
╠══════════════════════════════════════════════════════╣
║  Results saved to data/backtest/
╚══════════════════════════════════════════════════════╝

BY PATTERN:""")
    for pattern, s in r["by_pattern"].items():
        if s["count"] > 0:
            print(f"  {pattern:<25} {s['count']:>3} trades  "
                  f"WR: {s['win_rate']:.0f}%  Avg R: {s['avg_r']:.2f}")

    print("\nBY REGIME:")
    for regime, s in r["by_regime"].items():
        if s["count"] > 0:
            print(f"  {regime:<10} {s['count']:>3} trades  "
                  f"WR: {s['win_rate']:.0f}%  Avg R: {s['avg_r']:.2f}")

    print("\nTOP TICKERS BY R GENERATED:")
    for t in r["top_tickers"][:10]:
        print(f"  {t['ticker']:<6} {t['total_r']:.1f}R total  "
              f"{t['trades']} trades  WR: {t['win_rate']:.0f}%")


if __name__ == "__main__":
    main()
