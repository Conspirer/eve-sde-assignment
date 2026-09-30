"""Shared test setup.

Every test gets a brand-new, empty database, so tests never affect each other.
By default that's in-memory SQLite. To run on PostgreSQL, see the README
("Run the tests"). It uses a separate `eve_test` database, because the tests
delete every table when they finish.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import rate_limit, redis_client
from app.database import Base, get_db
from app.main import app
from app.models import CentreTest, DiagnosticCentre, DiagnosticTest, User
from app.security import create_access_token, hash_password, sign_webhook_body

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "sqlite://")
if TEST_DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        TEST_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
else:
    # Safety net: never run the tests (which drop all tables) on a real database.
    assert TEST_DATABASE_URL.rsplit("/", 1)[-1].endswith("_test"), "TEST_DATABASE_URL must point to a *_test database"
    engine = create_engine(TEST_DATABASE_URL)
TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(autouse=True)
def no_redis_by_default(monkeypatch):
    """Tests run without Redis unless they ask for `fake_redis`,
    and every test starts with zero rate-limit counts."""
    monkeypatch.setattr(redis_client, "client", None)
    rate_limit.reset_memory_counts()


@pytest.fixture
def fake_redis(monkeypatch):
    """An in-memory stand-in for Redis (the fakeredis library)."""
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_client, "client", fake)
    return fake


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = TestingSession()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db):
    def override_get_db():
        # A new session per request, exactly like the real get_db.
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def make_user(db, email="user@example.com", is_admin=False) -> User:
    user = User(email=email, full_name="Test User",
                hashed_password=hash_password("password123"), is_admin=is_admin)
    db.add(user)
    db.commit()
    return user


def auth_headers(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.fixture
def user(db):
    return make_user(db)


@pytest.fixture
def user_headers(user):
    return auth_headers(user)


@pytest.fixture
def other_headers(db):
    return auth_headers(make_user(db, email="other@example.com"))


@pytest.fixture
def admin_headers(db):
    return auth_headers(make_user(db, email="admin@example.com", is_admin=True))


@pytest.fixture
def centre_test(db) -> CentreTest:
    """A centre in Mumbai offering a CBC test for 500.00."""
    centre = DiagnosticCentre(name="EVE Andheri", city="Mumbai", address="Link Road")
    test = DiagnosticTest(name="Complete Blood Count")
    ct = CentreTest(centre=centre, test=test, price=Decimal("500.00"))
    db.add(ct)
    db.commit()
    return ct


def future_time(days=2) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


@pytest.fixture
def booking(client, user_headers, centre_test) -> dict:
    response = client.post("/bookings/", headers=user_headers, json={
        "centre_id": centre_test.centre_id,
        "test_id": centre_test.test_id,
        "appointment_at": future_time(),
    })
    assert response.status_code == 201
    return response.json()


def pay(client, headers, booking_id, outcome):
    return client.post("/payments/", headers=headers,
                       json={"booking_id": booking_id, "simulate_outcome": outcome})


def send_webhook(client, payload: dict, signature: str | None = "auto"):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signature == "auto":
        signature = sign_webhook_body(body)
    if signature is not None:
        headers["X-Webhook-Signature"] = signature
    return client.post("/payments/webhook/", content=body, headers=headers)


@pytest.fixture
def simulate_race(monkeypatch):
    """Call `simulate_race()` right before a request to make its next
    "does it already exist?" query answer "no", as if another request
    inserted the same row a moment later. The database's UNIQUE constraint
    must then be what saves us."""
    from sqlalchemy.orm import Session

    real_scalar = Session.scalar

    def arm():
        state = {"used": False}

        def scalar_once_none(self, *args, **kwargs):
            if not state["used"]:
                state["used"] = True
                return None
            return real_scalar(self, *args, **kwargs)

        monkeypatch.setattr(Session, "scalar", scalar_once_none)

    return arm
