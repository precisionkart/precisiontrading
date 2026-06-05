"""Lock the RS-universe-skew fix (Phase 7): the My-Picks RS reference must be the
broad, NON-RVOL-gated universe, not the ~28-name RVOL>2 targets universe."""

from pinpoint.config import RS_REFERENCE_SCREEN, TARGETS_SCREEN


def test_rs_reference_is_broad_not_rvol_gated():
    # the targets screen IS rvol-gated; the RS reference must NOT be
    assert "Relative Volume" in TARGETS_SCREEN
    assert "Relative Volume" not in RS_REFERENCE_SCREEN


def test_rs_reference_is_just_liquidity_and_near_high():
    assert set(RS_REFERENCE_SCREEN) == {"Price", "Average Volume", "52-Week High/Low"}
    assert RS_REFERENCE_SCREEN["52-Week High/Low"] == "0-10% below High"
