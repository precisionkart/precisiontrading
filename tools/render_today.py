"""tools/render_today.py — render the Phase-4 showcase report for 2026-06-06.

Focus = AAPL + GOOGL graded through the analyzer (they qualify on R:R>=5 but sit
outside today's RVOL-gated live universe); Targets = today's live AGX/BAND/MEC;
Earnings = today's live gapped-up reactions. RS for the Focus picks is ranked
against a broad reference universe (Section 7B fidelity note).
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import scan
from pinpoint import pipeline, themes
from pinpoint import ipo as ipo_mod
from pinpoint import regime as regime_mod
from pinpoint.finviz_client import FinvizClient


def main() -> None:
    client = FinvizClient()
    reg = regime_mod.fetch_regime(client)
    print(reg.banner())

    theme_ctx = themes.build_theme_context(client)
    scan._print_theme_rankings(theme_ctx)

    ipo_res = ipo_mod.build_ipo_watchlist(client)

    # Live Targets (AGX/BAND/MEC today) + Earnings, with theme/IPO layers.
    uni = pipeline.fetch_targets_universe(client, reg, theme_ctx=theme_ctx,
                                          ipo_ctx=ipo_res.ipo_ctx)
    ern = pipeline.run_earnings(client)

    # Broad reference universe for a meaningful RS percentile.
    from pinpoint.config import RS_REFERENCE_SCREEN
    ref = client.fetch_universe(RS_REFERENCE_SCREEN, views=("performance",))
    ref_norm = pipeline.normalize_universe(ref.df)
    print(f"RS reference universe: {len(ref_norm)} names")

    # Focus = AAPL + GOOGL graded through the same engine (demo render).
    focus = pipeline.analyze_tickers(client, ["AAPL", "GOOGL"], reg,
                                     reference_universe=ref_norm, theme_ctx=theme_ctx,
                                     ipo_ctx=ipo_res.ipo_ctx)
    print(f"Focus qualifiers: {list(focus['ticker']) if len(focus) else '(none)'}")

    date = "2026-06-06"
    as_of = datetime.now().strftime("%H:%M")
    scan.render_outputs(focus, uni.df, ern.df, reg, date, as_of,
                        ipo=ipo_res.watchlist, demo=True)


if __name__ == "__main__":
    main()
