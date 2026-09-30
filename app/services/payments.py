"""Payment logic shared by POST /payments/ and the webhook."""
import logging
import random
import uuid

from app.models import BookingStatus, Payment, PaymentStatus
from app.services.booking_state import can_transition

logger = logging.getLogger(__name__)


def new_provider_reference() -> str:
    return f"pay_{uuid.uuid4().hex}"


def simulate_gateway(requested_outcome: str | None) -> PaymentStatus:
    """Pretend to call a real gateway (Razorpay, Stripe...).

    If the caller asked for a specific result we return it, which keeps tests
    predictable. Otherwise ~80% of payments succeed.
    """
    if requested_outcome is not None:
        return PaymentStatus(requested_outcome)
    return PaymentStatus.SUCCESS if random.random() < 0.8 else PaymentStatus.FAILED


def apply_payment_status(payment: Payment, new_status: PaymentStatus) -> None:
    """Update a payment and its booking. The ONLY place that does this.

    Rules:
      1. Same status again            -> do nothing (safe to call twice).
      2. Payment already SUCCESS      -> never change it (money has moved;
                                         undoing it would be a refund flow).
      3. Otherwise update the payment, then move the booking to CONFIRMED /
         FAILED *only if the state machine allows it*. For example, a late
         "SUCCESS" for a booking that was already CANCELLED leaves the booking
         CANCELLED and logs a warning so someone can issue a refund.
    """
    if payment.status == new_status:
        return

    if payment.status == PaymentStatus.SUCCESS:
        logger.warning(
            "Ignoring status %s for payment %s: it already succeeded",
            new_status.value, payment.provider_reference,
        )
        return

    payment.status = new_status

    booking = payment.booking
    target = BookingStatus.CONFIRMED if new_status == PaymentStatus.SUCCESS else BookingStatus.FAILED

    if booking.status == target:
        if new_status == PaymentStatus.SUCCESS:
            # This attempt succeeded, but another attempt had already
            # confirmed the booking: the user has paid twice.
            logger.warning(
                "Booking %s already confirmed; payment %s is a duplicate (needs refund)",
                booking.id, payment.provider_reference,
            )
        return
    if can_transition(booking.status, target):
        booking.status = target
    else:
        logger.warning(
            "Payment %s is %s but booking %s is %s; booking left unchanged%s",
            payment.provider_reference, new_status.value, booking.id, booking.status.value,
            " (needs refund)" if new_status == PaymentStatus.SUCCESS else "",
        )
