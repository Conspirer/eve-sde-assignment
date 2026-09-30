"""Database tables.

    users ──< bookings >── diagnostic_centres ──< centre_tests >── diagnostic_tests
                 │
                 └──< payments          webhook_events (idempotency log)

`centre_tests` is the join table: it says "centre X offers test Y at price Z".
"""
import enum
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BookingStatus(str, enum.Enum):
    PENDING = "PENDING"        # created, waiting for payment
    CONFIRMED = "CONFIRMED"    # paid
    FAILED = "FAILED"          # last payment attempt failed (user may retry)
    CANCELLED = "CANCELLED"    # cancelled by the user


class PaymentStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(100))
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    bookings: Mapped[list["Booking"]] = relationship(back_populates="user")


class DiagnosticCentre(Base):
    __tablename__ = "diagnostic_centres"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(100), index=True)
    address: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tests: Mapped[list["CentreTest"]] = relationship(
        back_populates="centre", cascade="all, delete-orphan"
    )


class DiagnosticTest(Base):
    """A test in the global catalogue, e.g. "Complete Blood Count". No price here:
    the price depends on the centre, so it lives in `CentreTest`."""

    __tablename__ = "diagnostic_tests"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str | None] = mapped_column(Text, default=None)


class CentreTest(Base):
    """Centre X offers test Y at price Z."""

    __tablename__ = "centre_tests"
    __table_args__ = (
        UniqueConstraint("centre_id", "test_id", name="uq_centre_test"),
        CheckConstraint("price > 0", name="ck_centre_test_price_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    centre_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_centres.id", ondelete="CASCADE"))
    test_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_tests.id", ondelete="CASCADE"))
    # Numeric, never float, for money: floats can't store 0.1 exactly.
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    centre: Mapped[DiagnosticCentre] = relationship(back_populates="tests")
    test: Mapped[DiagnosticTest] = relationship()


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    centre_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_centres.id"))
    test_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_tests.id"))
    appointment_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # A copy of the price at booking time, so later price changes
    # don't change what this user owes.
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus, name="booking_status"), default=BookingStatus.PENDING, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="bookings")
    centre: Mapped[DiagnosticCentre] = relationship()
    test: Mapped[DiagnosticTest] = relationship()
    payments: Mapped[list["Payment"]] = relationship(back_populates="booking")


class Payment(Base):
    """One payment *attempt*. A booking can have several (e.g. fail, then retry)."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status"), default=PaymentStatus.PENDING
    )
    # The id the payment provider knows this payment by. Webhooks refer to it.
    provider_reference: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    booking: Mapped[Booking] = relationship(back_populates="payments")


class WebhookEvent(Base):
    """Every webhook event we have processed.

    The UNIQUE constraint on `event_id` is what makes the webhook idempotent:
    the database itself refuses to store the same event twice.
    """

    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(100), unique=True)
    payment_reference: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
