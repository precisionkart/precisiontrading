"""Tests for BUG 1 — scan.py's in-scan Telegram is OFF by default.

telegram_schedule now owns all messaging; scan.py only refreshes the cache and
must stay silent unless SCAN_PY_TELEGRAM is explicitly set. The send capability
(send_scan_telegram) is kept but only called behind the flag.
"""
import importlib

import scan


def test_scan_py_telegram_off_by_default():
    # silent by default — a plain `scan.py --all` sends nothing to Telegram
    assert scan.SCAN_PY_TELEGRAM is False


def test_send_scan_telegram_still_exists():
    # capability is gated, NOT deleted (so it can be re-armed)
    assert callable(scan.send_scan_telegram)


def test_flag_is_armed_by_env(monkeypatch):
    # the flag mechanism works: SCAN_PY_TELEGRAM=true re-enables the legacy sends
    monkeypatch.setenv("SCAN_PY_TELEGRAM", "true")
    reloaded = importlib.reload(scan)
    try:
        assert reloaded.SCAN_PY_TELEGRAM is True
    finally:
        monkeypatch.delenv("SCAN_PY_TELEGRAM", raising=False)
        importlib.reload(reloaded)          # restore default-off for other tests
