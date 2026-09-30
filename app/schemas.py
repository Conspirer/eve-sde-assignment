"""Request and response shapes (Pydantic).

Pydantic validates every incoming request against these classes. If the data
is wrong (missing field, bad email, negative price...) FastAPI automatically
returns 422 with a clear error, before our code even runs.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar

from fastapi import Path
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
)

from app.models import BookingStatus, PaymentStatus

T = TypeVar("T")

# Largest value an INTEGER id column can hold. Bigger ids can't exist, and
# SQLite even crashes if asked for one, so they're rejected as invalid (422).
MAX_ID = 2_147_483_647
Id = Annotated[int, Field(ge=1, le=MAX_ID)]          # for ids in a JSON body
IdPath = Annotated[int, Path(ge=1, le=MAX_ID)]       # for ids in the URL

# How far ahead an appointment can be booked.
MAX_BOOKING_DAYS_AHEAD = 365


def trimmed(max_length: int) -> StringConstraints:
    """Text with surrounding spaces removed, so "   " counts as empty and is rejected."""
    return StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)


class ORMModel(BaseModel):
    # Lets Pydantic read values straight from SQLAlchemy objects.
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    skip: int
    limit: int


# ---------- Auth ----------

class SignupRequest(BaseModel):
    email: EmailStr
    full_name: Annotated[str, trimmed(100)]
    password: str = Field(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def fits_bcrypt(cls, value: str) -> str:
        # bcrypt accepts at most 72 *bytes*. Characters like "é" take 2 bytes,
        # so a 72-character password can still be too long.
        if len(value.encode()) > 72:
            raise ValueError("password is too long")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(ORMModel):
    id: int
    email: EmailStr
    full_name: str
    is_admin: bool


# ---------- Centres & tests ----------

class TestCreate(BaseModel):
    name: Annotated[str, trimmed(200)]
    description: str | None = None


class TestOut(ORMModel):
    id: int
    name: str
    description: str | None


class CentreCreate(BaseModel):
    name: Annotated[str, trimmed(200)]
    city: Annotated[str, trimmed(100)]
    address: Annotated[str, trimmed(500)]


class CentreOut(ORMModel):
    id: int
    name: str
    city: str
    address: str


class CentreTestCreate(BaseModel):
    test_id: Id
    price: Decimal = Field(gt=0, max_digits=10, decimal_places=2)


class CentreTestOut(ORMModel):
    test: TestOut
    price: Decimal


class CentreDetailOut(CentreOut):
    tests: list[CentreTestOut]


# ---------- Bookings ----------

class BookingCreate(BaseModel):
    centre_id: Id
    test_id: Id
    appointment_at: datetime

    @field_validator("appointment_at")
    @classmethod
    def must_be_in_future(cls, value: datetime) -> datetime:
        if value.tzinfo is None:  # treat times without a timezone as UTC
            value = value.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if value <= now:
            raise ValueError("appointment_at must be in the future")
        if value > now + timedelta(days=MAX_BOOKING_DAYS_AHEAD):
            raise ValueError(f"appointment_at must be within {MAX_BOOKING_DAYS_AHEAD} days")
        # Always store UTC. Convert to local time only when displaying.
        return value.astimezone(timezone.utc)


class BookingOut(ORMModel):
    id: int
    user_id: int
    centre_id: int
    test_id: int
    appointment_at: datetime
    amount: Decimal
    status: BookingStatus
    created_at: datetime


# ---------- Payments ----------

class PaymentCreate(BaseModel):
    booking_id: Id
    # Lets you (or a test) choose the simulated result.
    # Leave it out and the fake gateway decides randomly.
    simulate_outcome: Literal["SUCCESS", "FAILED"] | None = None


class PaymentOut(ORMModel):
    id: int
    booking_id: int
    amount: Decimal
    status: PaymentStatus
    provider_reference: str
    created_at: datetime


class WebhookPayload(BaseModel):
    event_id: str = Field(min_length=1, max_length=100)
    payment_reference: str = Field(min_length=1, max_length=64)
    status: Literal["SUCCESS", "FAILED"]


class WebhookResponse(BaseModel):
    detail: str
