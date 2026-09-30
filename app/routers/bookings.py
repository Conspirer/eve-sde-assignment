from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Booking, BookingStatus, CentreTest, DiagnosticCentre, User
from app.pagination import PageParams, paginate
from app.schemas import BookingCreate, BookingOut, IdPath, Page
from app.security import get_current_user
from app.services.booking_state import can_transition

router = APIRouter(prefix="/bookings", tags=["bookings"])


def get_owned_booking(db: Session, booking_id: int, user: User, lock: bool = False) -> Booking:
    """Load a booking the user is allowed to see, or raise 404.

    Someone else's booking also gives 404 (not 403), so users can't discover
    which booking ids exist by trying them one by one.
    `lock=True` locks the row (SELECT ... FOR UPDATE) until commit, so two
    requests can't change the same booking at the same moment.
    """
    query = select(Booking).where(Booking.id == booking_id)
    if lock:
        query = query.with_for_update()
    booking = db.scalar(query)
    if booking is None or (booking.user_id != user.id and not user.is_admin):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")
    return booking


@router.post("/", response_model=BookingOut, status_code=status.HTTP_201_CREATED)
def create_booking(data: BookingCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if db.get(DiagnosticCentre, data.centre_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Centre not found")

    centre_test = db.scalar(
        select(CentreTest).where(CentreTest.centre_id == data.centre_id, CentreTest.test_id == data.test_id)
    )
    if centre_test is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This centre does not offer the selected test")

    # Stop accidental double bookings (e.g. user clicks "Book" twice).
    duplicate = db.scalar(
        select(Booking).where(
            Booking.user_id == user.id,
            Booking.centre_id == data.centre_id,
            Booking.test_id == data.test_id,
            Booking.appointment_at == data.appointment_at,
            Booking.status.in_([BookingStatus.PENDING, BookingStatus.CONFIRMED]),
        )
    )
    if duplicate:
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have this booking")

    booking = Booking(
        user_id=user.id,
        centre_id=data.centre_id,
        test_id=data.test_id,
        appointment_at=data.appointment_at,
        amount=centre_test.price,  # price comes from the DB, never from the client
        status=BookingStatus.PENDING,
    )
    db.add(booking)
    db.commit()
    return booking


@router.get("/", response_model=Page[BookingOut])
def list_bookings(
    status_filter: BookingStatus | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(Booking).order_by(Booking.id.desc())
    if not user.is_admin:
        query = query.where(Booking.user_id == user.id)
    if status_filter:
        query = query.where(Booking.status == status_filter)
    return paginate(db, query, params)


@router.get("/{booking_id}", response_model=BookingOut)
def get_booking(booking_id: IdPath, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return get_owned_booking(db, booking_id, user)


@router.post("/{booking_id}/cancel/", response_model=BookingOut)
def cancel_booking(booking_id: IdPath, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    booking = get_owned_booking(db, booking_id, user, lock=True)
    if not can_transition(booking.status, BookingStatus.CANCELLED):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A {booking.status.value} booking cannot be cancelled"
        )
    booking.status = BookingStatus.CANCELLED
    db.commit()
    return booking
