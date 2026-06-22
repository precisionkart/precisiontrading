"""Tests for monitor.py alert dedupe (BUG 2).

Mirrors the telegram_schedule dedupe tests: an alert event fires ONCE per
(ticker, kind) while a position stays OPEN; a different kind/ticker still fires;
a position closing clears its keys so a re-entry re-arms.
"""
import json

import monitor


def _alert(tk, kind="HARD_STOP", urgency="CRITICAL"):
    return {"ticker": tk, "kind": kind, "urgency": urgency,
            "message": f"{tk} below stop", "action": "exit full position"}


def test_same_stop_hit_twice_sends_once():
    notable = [_alert("DOCN")]
    to_send1, sent1 = monitor.select_to_send(notable, set(), {"DOCN"})
    assert [a["ticker"] for a in to_send1] == ["DOCN"]          # fires the first time
    assert "DOCN:HARD_STOP" in sent1
    # same stop, next cycle, persisted sent-set -> NO repeat
    to_send2, sent2 = monitor.select_to_send(notable, sent1, {"DOCN"})
    assert to_send2 == []
    assert sent2 == sent1


def test_different_kind_or_ticker_still_sends():
    sent = {"DOCN:HARD_STOP"}
    notable = [_alert("DOCN", "HARD_STOP"),             # already sent -> deduped
               _alert("DOCN", "PARABOLIC", "HIGH"),     # different kind -> sends
               _alert("NVDA", "HARD_STOP")]             # different ticker -> sends
    to_send, _ = monitor.select_to_send(notable, sent, {"DOCN", "NVDA"})
    keys = {f"{a['ticker']}:{a['kind']}" for a in to_send}
    assert keys == {"DOCN:PARABOLIC", "NVDA:HARD_STOP"}


def test_close_then_reopen_rearms():
    notable = [_alert("DOCN")]
    _, sent = monitor.select_to_send(notable, set(), {"DOCN"})         # open + fire
    assert "DOCN:HARD_STOP" in sent
    # position closes -> ticker no longer OPEN -> key pruned
    _, sent_closed = monitor.select_to_send([], sent, set())
    assert "DOCN:HARD_STOP" not in sent_closed
    # re-open -> re-armed, fires again
    to_send, _ = monitor.select_to_send(notable, sent_closed, {"DOCN"})
    assert [a["ticker"] for a in to_send] == ["DOCN"]


def test_prune_sent_drops_closed_tickers():
    sent = {"DOCN:HARD_STOP", "NVDA:PARABOLIC"}
    assert monitor.prune_sent(sent, {"NVDA"}) == {"NVDA:PARABOLIC"}
    assert monitor.prune_sent(sent, set()) == set()


def test_state_file_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "monitor_state.json"
    monkeypatch.setattr(monitor, "MONITOR_STATE_PATH", str(path))
    monitor._save_sent({"DOCN:HARD_STOP", "NVDA:HARD_STOP"})
    assert path.exists()                                   # state file persisted
    on_disk = json.loads(path.read_text())
    assert sorted(on_disk["sent"]) == ["DOCN:HARD_STOP", "NVDA:HARD_STOP"]
    assert monitor._load_sent() == {"DOCN:HARD_STOP", "NVDA:HARD_STOP"}
