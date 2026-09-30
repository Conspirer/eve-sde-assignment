"""Two identical requests at the same instant.

Each endpoint checks "does it already exist?" first. In a race, both requests
pass that check, and the UNIQUE constraint in the database has to reject the
second insert. These tests force that situation with `simulate_race`.
"""
from sqlalchemy import func, select

from app.models import CentreTest, DiagnosticTest, User, WebhookEvent
from tests.conftest import pay, send_webhook

SIGNUP = {"email": "race@example.com", "full_name": "Race", "password": "password123"}


def test_signup_race_returns_409(client, db, simulate_race):
    assert client.post("/auth/signup/", json=SIGNUP).status_code == 201
    simulate_race()
    response = client.post("/auth/signup/", json=SIGNUP)
    assert response.status_code == 409
    assert db.scalar(select(func.count()).select_from(User).where(User.email == SIGNUP["email"])) == 1


def test_create_test_race_returns_409(client, db, admin_headers, simulate_race):
    assert client.post("/tests/", headers=admin_headers, json={"name": "CBC"}).status_code == 201
    simulate_race()
    assert client.post("/tests/", headers=admin_headers, json={"name": "CBC"}).status_code == 409
    assert db.scalar(select(func.count()).select_from(DiagnosticTest)) == 1


def test_add_test_to_centre_race_returns_409(client, db, admin_headers, centre_test, simulate_race):
    simulate_race()
    response = client.post(f"/centres/{centre_test.centre_id}/tests/", headers=admin_headers,
                           json={"test_id": centre_test.test_id, "price": "10.00"})
    assert response.status_code == 409
    assert db.scalar(select(func.count()).select_from(CentreTest)) == 1


def test_webhook_race_is_still_idempotent(client, db, booking, user_headers, simulate_race):
    reference = pay(client, user_headers, booking["id"], "FAILED").json()["provider_reference"]
    event = {"event_id": "evt_race", "payment_reference": reference, "status": "SUCCESS"}
    assert send_webhook(client, event).json()["detail"] == "Event processed"

    simulate_race()  # the second copy doesn't see the first one...
    response = send_webhook(client, event)
    assert response.status_code == 200
    assert response.json()["detail"] == "Event already processed"  # ...but the DB stops it
    assert db.scalar(select(func.count()).select_from(WebhookEvent)) == 1
