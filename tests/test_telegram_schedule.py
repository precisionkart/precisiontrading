"""Tests for the NEW Telegram schedule (Phase 12) — pure diff + flag default."""
from pinpoint import telegram_schedule as ts


def _setup(tk, score, rs, entry, stop, price, tier="Good", rr=6.0):
    return {tk: {"ticker": tk, "score": score, "rs": rs, "entry": entry, "stop": stop,
                 "rr": rr, "price": price, "pattern": "flag", "tier": tier, "sector": "Tech"}}


def test_flag_defaults_off():
    # default must be False so the live bot is unchanged until explicitly armed
    assert ts.NEW_TELEGRAM_SCHEDULE is False


def test_trigger_fires_once_then_deduped():
    setups = _setup("AAA", 80, 95, entry=100.0, stop=95.0, price=101.0)   # price >= entry
    prev = ts.establish_baseline(setups)                                   # baseline, no alerts
    alerts1, state1 = ts.detect_changes(prev, setups, [])
    assert any("TRIGGERED" in a and "AAA" in a for a in alerts1)
    # same state next cycle -> NO repeat
    alerts2, _ = ts.detect_changes(state1, setups, [])
    assert not any("TRIGGERED" in a for a in alerts2)


def test_new_and_dropped_focus():
    setups = _setup("NEW", 70, 90, 50.0, 47.0, 48.0)     # price < entry (no trigger)
    prev = ts.establish_baseline(_setup("OLD", 70, 90, 50.0, 47.0, 48.0))
    alerts, _ = ts.detect_changes(prev, setups, [])
    assert any("NEW SETUP" in a and "NEW" in a for a in alerts)
    assert any("DROPPED" in a and "OLD" in a for a in alerts)


def test_big_move_threshold_and_tunable():
    base_setups = _setup("MOV", 70, 90, 50.0, 47.0, 48.0)
    prev = ts.establish_baseline(base_setups)
    # bump score by exactly the threshold -> alert
    bumped = _setup("MOV", 70 + ts.SCORE_MOVE_ALERT, 90, 50.0, 47.0, 48.0)
    alerts, _ = ts.detect_changes(prev, bumped, [])
    assert any("BIG MOVE" in a and "MOV" in a for a in alerts)
    # a sub-threshold move -> nothing
    small = _setup("MOV", 70 + ts.SCORE_MOVE_ALERT - 1, 90, 50.0, 47.0, 48.0)
    alerts2, _ = ts.detect_changes(prev, small, [])
    assert not any("BIG MOVE" in a for a in alerts2)


def test_position_exit_reused_and_deduped():
    setups = _setup("POS", 70, 90, 50.0, 47.0, 48.0)
    prev = ts.establish_baseline(setups)
    pa = [{"ticker": "POS", "kind": "HARD_STOP", "message": "below stop", "action": "exit"}]
    alerts1, state1 = ts.detect_changes(prev, setups, pa)
    assert any("HARD_STOP" in a and "POS" in a for a in alerts1)
    alerts2, _ = ts.detect_changes(state1, setups, pa)          # same alert -> deduped
    assert not any("HARD_STOP" in a for a in alerts2)


def test_quiet_cycle_sends_nothing():
    setups = _setup("Q", 70, 90, 50.0, 47.0, 48.0)             # price<entry, no change
    prev = ts.establish_baseline(setups)
    alerts, _ = ts.detect_changes(prev, setups, [])
    assert alerts == []


def test_after_hours_empty_returns_none():
    assert ts.after_hours_text([]) is None
    assert ts.after_hours_text([{"ticker": "X", "price": 10.0, "pct": 5.0}]) is not None


def test_baseline_reference_no_new_setup_spam():
    # diffing the SAME list against a freshly-established baseline -> silence,
    # NOT a "new setup" alert for every existing name.
    setups = {**_setup("A", 80, 95, 50, 47, 48), **_setup("B", 70, 90, 60, 57, 58),
              **_setup("C", 66, 88, 30, 28, 29)}
    prev = ts.establish_baseline(setups)
    alerts, _ = ts.detect_changes(prev, setups, [])
    assert alerts == []


def test_new_entry_not_double_reported_as_big_move():
    # A name that just ENTERED Focus must fire NEW SETUP only — never BIG MOVE,
    # even if a baseline entry exists with a large delta.
    prev = ts.establish_baseline(_setup("OLD", 70, 90, 50, 47, 48))   # focus = [OLD]
    prev["baseline"]["NEWN"] = {"score": 50, "rs": 70}               # big gap vs current
    setups = _setup("NEWN", 70, 90, 50, 47, 48)                      # +20 score, +20 RS
    alerts, _ = ts.detect_changes(prev, setups, [])
    assert any("NEW SETUP" in a and "NEWN" in a for a in alerts)
    assert not any("BIG MOVE" in a and "NEWN" in a for a in alerts)


def test_thresholds_are_safer_defaults():
    assert ts.SCORE_MOVE_ALERT >= 15.0 and ts.RS_MOVE_ALERT >= 10.0
