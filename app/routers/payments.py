import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Booking, Payment, PaymentStatus, User, WebhookEvent
from app.routers.bookings import get_owned_booking
from app.schemas import PaymentCreate, PaymentOut, WebhookPayload, WebhookResponse
from app.security import get_current_user, is_valid_webhook_signature
from app.services.booking_state import PAYABLE_STATUSES
from app.services.payments import (
    apply_payment_status,
    new_provider_reference,
    simulate_gateway,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
def create_payment(data: PaymentCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Pay for a booking using the simulated gateway.

    Returns 201 even if the payment FAILED: a payment attempt *was* created,
    and its `status` field says how it went.
    """
    # Lock the booking row so two "Pay" clicks at the same time can't both
    # charge the user: the second request waits, then sees CONFIRMED -> 409.
    booking = get_owned_booking(db, data.booking_id, user, lock=True)

    if booking.status not in PAYABLE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Booking is {booking.status.value} and cannot be paid"
        )

    payment = Payment(
        booking=booking,
        amount=booking.amount,  # amount comes from the booking, not the client
        status=PaymentStatus.PENDING,
        provider_reference=new_provider_reference(),
    )
    db.add(payment)

    result = simulate_gateway(data.simulate_outcome)
    apply_payment_status(payment, result)
    db.commit()

    logger.info("payment=%s booking=%s status=%s", payment.provider_reference, booking.id, payment.status.value)
    return payment


async def read_raw_body(request: Request) -> bytes:
    # We need the exact bytes the provider sent to check the signature,
    # so we read the body ourselves instead of letting FastAPI parse it.
    return await request.body()


@router.post(
    "/webhook/",
    response_model=WebhookResponse,
    # Show the expected body in Swagger even though we parse it manually.
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": WebhookPayload.model_json_schema()}},
        }
    },
)
def payment_webhook(
    body: bytes = Depends(read_raw_body),
    x_webhook_signature: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Payment provider tells us the final status of a payment.

    Idempotent: sending the same event (same `event_id`) any number of times
    has the same effect as sending it once.
    """
    # 1. Is this really from the provider?
    if not is_valid_webhook_signature(body, x_webhook_signature):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature")

    # 2. Is the body valid?
    try:
        event = WebhookPayload.model_validate_json(body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from None

    # 3. Seen this event before? Then do nothing, but still answer 200 so the
    #    provider stops re-sending it.
    if db.scalar(select(WebhookEvent).where(WebhookEvent.event_id == event.event_id)):
        logger.info("webhook event=%s duplicate, ignored", event.event_id)
        return WebhookResponse(detail="Event already processed")

    # 4. Find the payment (locked, so nothing else changes it meanwhile).
    payment = db.scalar(
        select(Payment).where(Payment.provider_reference == event.payment_reference).with_for_update()
    )
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    # Also lock the booking: a retry through POST /payments/ may be
    # changing it at this exact moment.
    db.scalar(select(Booking).where(Booking.id == payment.booking_id).with_for_update())

    # 5. Record the event and apply it in ONE transaction: either both
    #    happen or neither does.
    db.add(WebhookEvent(
        event_id=event.event_id,
        payment_reference=event.payment_reference,
        status=event.status,
    ))
    apply_payment_status(payment, PaymentStatus(event.status))

    try:
        db.commit()
    except IntegrityError:
        # Two copies of the same event arrived at the same instant and both
        # passed step 3. The UNIQUE(event_id) constraint let only one insert
        # win; this one is rolled back, so nothing is applied twice.
        db.rollback()
        logger.info("webhook event=%s duplicate (race), ignored", event.event_id)
        return WebhookResponse(detail="Event already processed")

    logger.info("webhook event=%s payment=%s status=%s processed", event.event_id, payment.provider_reference, event.status)
    return WebhookResponse(detail="Event processed")
