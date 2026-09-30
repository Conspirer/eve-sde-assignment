"""The booking state machine: which status changes are allowed.

    PENDING ──pay ok──▶ CONFIRMED ──cancel──▶ CANCELLED
       │                    ▲                     ▲
       └──pay fails──▶ FAILED ──retry ok──┘       │
       │                  └──────cancel───────────┤
       └──────────────────cancel──────────────────┘

Keeping the rules in one dict means no endpoint can put a booking into a
nonsense state, e.g. CANCELLED -> CONFIRMED or CONFIRMED -> FAILED.
"""
from app.models import BookingStatus

ALLOWED_TRANSITIONS: dict[BookingStatus, set[BookingStatus]] = {
    BookingStatus.PENDING: {BookingStatus.CONFIRMED, BookingStatus.FAILED, BookingStatus.CANCELLED},
    BookingStatus.FAILED: {BookingStatus.CONFIRMED, BookingStatus.CANCELLED},
    BookingStatus.CONFIRMED: {BookingStatus.CANCELLED},
    BookingStatus.CANCELLED: set(),  # final: nothing can happen after cancel
}

# A booking can be paid for only in these states.
PAYABLE_STATUSES = {BookingStatus.PENDING, BookingStatus.FAILED}


def can_transition(current: BookingStatus, new: BookingStatus) -> bool:
    return new in ALLOWED_TRANSITIONS[current]
