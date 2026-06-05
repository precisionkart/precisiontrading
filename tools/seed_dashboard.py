"""tools/seed_dashboard.py — seed store.py with today's scan so the Streamlit
Morning Dashboard / Full Scan render with data for screenshots.

Focus = AAPL/GOOGL graded via the analyzer (today's gated live Focus is empty —
AGX/BAND/MEC are extended); Targets/Earnings/IPO = live; watchlist seeded with a
couple names so the dashboard's watchlist section is populated.
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pinpoint import pipeline, store, themes
from pinpoint import ipo as ipo_mod
from pinpoint import regime as regime_mod
from pinpoint.finviz_client import FinvizClient


def main() -> None:
    client = FinvizClient()
    reg = regime_mod.fetch_regime(client)
    theme_ctx = themes.build_theme_context(client)
    ipo_res = ipo_mod.build_ipo_watchlist(client)

    uni = pipeline.fetch_targets_universe(client, reg, theme_ctx=theme_ctx,
                                          ipo_ctx=ipo_res.ipo_ctx)
    ern = pipeline.run_earnings(client)

    from pinpoint.config import RS_REFERENCE_SCREEN
    ref = client.fetch_universe(RS_REFERENCE_SCREEN, views=("performance",))
    ref_norm = pipeline.normalize_universe(ref.df)

    focus = pipeline.analyze_tickers(client, ["AAPL", "GOOGL"], reg,
                                     reference_universe=ref_norm, theme_ctx=theme_ctx,
                                     ipo_ctx=ipo_res.ipo_ctx)

    as_of = datetime.now().strftime("%H:%M")
    # RS reference for My-Picks must be BROAD (not the RVOL-gated targets), per 7B.
    store.save_universe_snapshot(ref_norm)
    store.save_scan_cache({"focus": focus, "targets": uni.df, "earnings": ern.df,
                           "ipo": ipo_res.watchlist}, reg, as_of)
    store.save_watchlist(["NVDA", "MEC"])
    print(f"seeded: {len(focus)} focus, {len(uni.df)} targets, {len(ern.df)} earnings, "
          f"{len(ipo_res.watchlist)} ipo; watchlist [NVDA, MEC]; as_of {as_of}")


if __name__ == "__main__":
    main()
