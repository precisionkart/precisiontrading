"""Entry point — persistent sidebar nav over five pages (Phase 7.6).

Sets page config + CSS once, loads the scan into shared session state, builds the
sidebar (logo · nav · regime/as-of/refresh), and runs the selected page via
st.navigation. Deploy main-file path stays app/Home.py.
"""

import streamlit as st

import common as c
from pinpoint import store

c.page_config("Pinpoint")
c.inject_css()
CLOUD = c.cloud_mode()


def _load_scan():
    if CLOUD:
        return c.load_published()
    cache = store.load_scan_cache()
    if cache is None or not cache.is_today:
        return None
    return {"regime": c.RegimeView(cache.regime_state, cache.regime_rationale),
            "theme_ctx": None, "themes": cache.themes,
            "focus": cache.lists.get("focus"), "targets": cache.lists.get("targets"),
            "earnings": cache.lists.get("earnings"), "ipo": cache.lists.get("ipo"),
            "as_of": cache.as_of, "warnings": []}


if "scan" not in st.session_state:
    st.session_state["scan"] = _load_scan()
scan = st.session_state["scan"]

dashboard = st.Page("views/dashboard.py", title="Dashboard",
                    icon=":material/space_dashboard:", default=True)
mypicks = st.Page("views/my_picks.py", title="My Picks", icon=":material/insights:")
fullscan = st.Page("views/full_scan.py", title="Full Scan", icon=":material/search:")
watchlist = st.Page("views/watchlist.py", title="Watchlist", icon=":material/star:")
settings = st.Page("views/settings.py", title="Settings", icon=":material/settings:")
nav = st.navigation([dashboard, mypicks, fullscan, watchlist, settings], position="hidden")

with st.sidebar:
    c.sidebar_logo()
    st.page_link(dashboard)
    st.page_link(mypicks)
    st.page_link(fullscan)
    st.page_link(watchlist)
    st.page_link(settings)
    c.sidebar_footer(scan, CLOUD)

nav.run()
