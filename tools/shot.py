"""tools/shot.py — Playwright screenshot helper (DEV ONLY, not a runtime dep).

Usage: python tools/shot.py <url> <out.png> [wait_selector] [timeout_ms]
Forces light color-scheme and waits for the app's content to render.
"""

import sys

from playwright.sync_api import sync_playwright


def main():
    url = sys.argv[1]
    out = sys.argv[2]
    wait_sel = sys.argv[3] if len(sys.argv) > 3 else ".pp-card, .pp-section, .pp-empty"
    timeout = int(sys.argv[4]) if len(sys.argv) > 4 else 90000

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 1400},
                                  color_scheme="light", device_scale_factor=2)
        page = ctx.new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout)
        try:
            page.wait_for_selector(wait_sel, timeout=timeout, state="visible")
        except Exception as exc:  # noqa: BLE001
            print(f"warn: selector wait failed: {exc}")
        page.wait_for_timeout(2500)        # let charts/dataframes settle
        page.screenshot(path=out, full_page=True)
        browser.close()
    print(f"saved {out}")


if __name__ == "__main__":
    main()
