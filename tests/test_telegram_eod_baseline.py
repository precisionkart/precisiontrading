"""Tests for BUG 3 — the EOD scan replaces the focus universe, so the baseline
must refresh at the EOD slot (and a wholesale swap is caught by a safety net) to
avoid a NEW SETUP storm on the next intraday run.
"""
from pinpoint import telegram_schedule as ts


def _setup(tk, score, rs, entry, stop, price, tier="Good", rr=6.0):
    return {tk: {"ticker": tk, "score": score, "rs": rs, "entry": entry, "stop": stop,
                 "rr": rr, "price": price, "pattern": "flag", "tier": tier, "sector": "Tech"}}


def _focus(*names):
    out = {}
    for i, n in enumerate(names):
        # price < entry so nothing TRIGGERS; scores/RS vary by name
        out.update(_setup(n, 70 + i, 90, 10 + i, 9 + i, 9.5 + i))
    return out


class _FakeBot:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True

    def send_eod_scan(self, **kwargs):
        return True


# ── (a) EOD slot re-baselines, and a following intraday run is silent ─────────
def test_eod_slot_rebaselines_on_post_eod_focus(monkeypatch):
    post_eod = _focus("N1", "N2", "N3")
    saved = {}
    monkeypatch.setattr(ts, "NEW_TELEGRAM_SCHEDULE", True)
    monkeypatch.setattr(ts, "gather_setups",
                        lambda cache: (post_eod, {"Elite": [], "Good": [], "Watchlist": []}))
    monkeypatch.setattr(ts, "_positions", lambda: [])
    monkeypatch.setattr(ts, "_sectors", lambda cache: [])
    monkeypatch.setattr(ts, "get_bot", lambda: _FakeBot())
    monkeypatch.setattr(ts, "save_state", lambda st: saved.update(st))
    monkeypatch.setattr("pinpoint.store.load_scan_cache", lambda: None)

    ts.dispatch_scheduled("eod", dry_run=False)

    # the EOD slot must have re-baselined onto the post-EOD focus universe
    assert sorted(saved.get("focus", [])) == sorted(post_eod)
    assert set(saved.get("baseline", {})) == set(post_eod)


def test_intraday_silent_after_eod_rebaseline(monkeypatch):
    post_eod = _focus("N1", "N2", "N3")
    state = ts.establish_baseline(post_eod)                 # what the EOD slot saved
    monkeypatch.setattr(ts, "load_state", lambda: state)
    monkeypatch.setattr(ts, "gather_setups", lambda cache: (post_eod, {}))
    monkeypatch.setattr(ts, "gather_position_alerts", lambda: [])
    monkeypatch.setattr("pinpoint.store.load_scan_cache", lambda: None)

    alerts = ts.run_intraday(dry_run=True)
    assert alerts == []                                     # no NEW SETUP storm


# ── (b) wholesale universe swap silently re-baselines, sends nothing ──────────
def test_universe_swap_silently_rebaselines(monkeypatch):
    prev = ts.establish_baseline(_focus("A", "B", "C"))     # morning baseline
    new_focus = _focus("X", "Y", "Z", "W")                  # all 4 new vs prev
    saved = {}
    bot = _FakeBot()
    monkeypatch.setattr(ts, "NEW_TELEGRAM_SCHEDULE", True)
    monkeypatch.setattr(ts, "load_state", lambda: prev)
    monkeypatch.setattr(ts, "gather_setups", lambda cache: (new_focus, {}))
    monkeypatch.setattr(ts, "gather_position_alerts", lambda: [])
    monkeypatch.setattr(ts, "get_bot", lambda: bot)
    monkeypatch.setattr(ts, "save_state", lambda st: saved.update(st))
    monkeypatch.setattr("pinpoint.store.load_scan_cache", lambda: None)

    alerts = ts.run_intraday(dry_run=False)
    assert alerts == []                                     # nothing fired
    assert bot.sent == []                                   # nothing sent live
    assert sorted(saved.get("focus", [])) == sorted(new_focus)   # re-baselined


# ── (c) a normal single new entry STILL fires (not over-suppressed) ───────────
def test_single_new_entry_still_fires(monkeypatch):
    prev = ts.establish_baseline(_focus("A", "B", "C"))
    cur = _focus("A", "B", "C")
    cur.update(_setup("D", 78, 93, 40, 39, 39.5))           # +1 new name (price < entry)
    monkeypatch.setattr(ts, "load_state", lambda: prev)
    monkeypatch.setattr(ts, "gather_setups", lambda cache: (cur, {}))
    monkeypatch.setattr(ts, "gather_position_alerts", lambda: [])
    monkeypatch.setattr("pinpoint.store.load_scan_cache", lambda: None)

    alerts = ts.run_intraday(dry_run=True)
    assert any("NEW SETUP" in a and "D" in a for a in alerts)
    # the established names must NOT be re-reported as new
    assert not any("NEW SETUP" in a and ("`A`" in a or "`B`" in a or "`C`" in a) for a in alerts)
