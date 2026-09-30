"""Unit tests for the state machine (no HTTP, no DB)."""
from app.models import BookingStatus as S
from app.services.booking_state import can_transition


def test_allowed_transitions():
    assert can_transition(S.PENDING, S.CONFIRMED)
    assert can_transition(S.PENDING, S.FAILED)
    assert can_transition(S.FAILED, S.CONFIRMED)   # retry payment
    assert can_transition(S.CONFIRMED, S.CANCELLED)


def test_forbidden_transitions():
    assert not can_transition(S.CONFIRMED, S.FAILED)
    assert not can_transition(S.CONFIRMED, S.PENDING)
    assert not can_transition(S.CANCELLED, S.CONFIRMED)
    assert not can_transition(S.CANCELLED, S.PENDING)
