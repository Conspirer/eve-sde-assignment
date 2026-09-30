from sqlalchemy import func, select

from app.models import Payment, PaymentStatus, WebhookEvent
from tests.conftest import pay, send_webhook


def booking_status(client, headers, booking_id):
    return client.get(f"/bookings/{booking_id}", headers=headers).json()["status"]


# ---------- POST /payments/ ----------

def test_successful_payment_confirms_booking(client, booking, user_headers):
    response = pay(client, user_headers, booking["id"], "SUCCESS")
    assert response.status_code == 201
    assert response.json()["status"] == "SUCCESS"
    assert response.json()["amount"] == "500.00"
    assert booking_status(client, user_headers, booking["id"]) == "CONFIRMED"


def test_failed_payment_marks_booking_failed(client, booking, user_headers):
    response = pay(client, user_headers, booking["id"], "FAILED")
    assert response.json()["status"] == "FAILED"
    assert booking_status(client, user_headers, booking["id"]) == "FAILED"


def test_retry_after_failed_payment(client, booking, user_headers):
    pay(client, user_headers, booking["id"], "FAILED")
    pay(client, user_headers, booking["id"], "SUCCESS")
    assert booking_status(client, user_headers, booking["id"]) == "CONFIRMED"


def test_cannot_pay_confirmed_booking_twice(client, booking, user_headers):
    pay(client, user_headers, booking["id"], "SUCCESS")
    assert pay(client, user_headers, booking["id"], "SUCCESS").status_code == 409


def test_cannot_pay_cancelled_booking(client, booking, user_headers):
    client.post(f"/bookings/{booking['id']}/cancel/", headers=user_headers)
    assert pay(client, user_headers, booking["id"], "SUCCESS").status_code == 409


def test_cannot_pay_someone_elses_booking(client, booking, other_headers):
    assert pay(client, other_headers, booking["id"], "SUCCESS").status_code == 404


def test_pay_invalid_booking_id(client, user_headers):
    assert pay(client, user_headers, 999, "SUCCESS").status_code == 404


def test_payment_requires_login(client, booking):
    assert client.post("/payments/", json={"booking_id": booking["id"]}).status_code == 401


# ---------- POST /payments/webhook/ ----------

def test_webhook_rejects_bad_or_missing_signature(client):
    payload = {"event_id": "evt_1", "payment_reference": "pay_x", "status": "SUCCESS"}
    assert send_webhook(client, payload, signature="wrong").status_code == 401
    assert send_webhook(client, payload, signature=None).status_code == 401


def test_webhook_invalid_payload(client):
    assert send_webhook(client, {"event_id": "evt_1"}).status_code == 422
    assert send_webhook(client, {"event_id": "e", "payment_reference": "p", "status": "MAYBE"}).status_code == 422


def test_webhook_unknown_payment(client):
    payload = {"event_id": "evt_1", "payment_reference": "pay_nope", "status": "SUCCESS"}
    assert send_webhook(client, payload).status_code == 404


def test_webhook_fixes_payment_that_actually_succeeded(client, db, booking, user_headers):
    """Our side saw FAILED, but the provider later says SUCCESS (e.g. slow bank)."""
    reference = pay(client, user_headers, booking["id"], "FAILED").json()["provider_reference"]

    response = send_webhook(client, {"event_id": "evt_1", "payment_reference": reference, "status": "SUCCESS"})
    assert response.status_code == 200
    assert response.json()["detail"] == "Event processed"
    assert booking_status(client, user_headers, booking["id"]) == "CONFIRMED"


def test_webhook_is_idempotent(client, db, booking, user_headers):
    reference = pay(client, user_headers, booking["id"], "FAILED").json()["provider_reference"]
    payload = {"event_id": "evt_same", "payment_reference": reference, "status": "SUCCESS"}

    first = send_webhook(client, payload)
    second = send_webhook(client, payload)
    third = send_webhook(client, payload)

    assert first.json()["detail"] == "Event processed"
    assert second.status_code == 200 and second.json()["detail"] == "Event already processed"
    assert third.json()["detail"] == "Event already processed"

    # Nothing was duplicated
    assert db.scalar(select(func.count()).select_from(WebhookEvent)) == 1
    assert db.scalar(select(func.count()).select_from(Payment)) == 1
    assert booking_status(client, user_headers, booking["id"]) == "CONFIRMED"


def test_webhook_cannot_undo_successful_payment(client, db, booking, user_headers):
    reference = pay(client, user_headers, booking["id"], "SUCCESS").json()["provider_reference"]

    send_webhook(client, {"event_id": "evt_late", "payment_reference": reference, "status": "FAILED"})
    assert booking_status(client, user_headers, booking["id"]) == "CONFIRMED"
    # Check the payment itself too: the booking would stay CONFIRMED anyway
    # (the state machine blocks CONFIRMED -> FAILED), so checking only the
    # booking would miss a payment wrongly flipped to FAILED.
    payment = db.scalar(select(Payment).where(Payment.provider_reference == reference))
    assert payment.status == PaymentStatus.SUCCESS


def test_webhook_does_not_revive_cancelled_booking(client, booking, user_headers):
    reference = pay(client, user_headers, booking["id"], "FAILED").json()["provider_reference"]
    client.post(f"/bookings/{booking['id']}/cancel/", headers=user_headers)

    send_webhook(client, {"event_id": "evt_1", "payment_reference": reference, "status": "SUCCESS"})
    assert booking_status(client, user_headers, booking["id"]) == "CANCELLED"


def test_webhook_invalid_json(client):
    from app.security import sign_webhook_body

    body = b"not json"
    response = client.post("/payments/webhook/", content=body,
                           headers={"X-Webhook-Signature": sign_webhook_body(body)})
    assert response.status_code == 422


def test_late_success_for_old_attempt_keeps_booking_confirmed(client, db, booking, user_headers):
    """Attempt 1 fails, attempt 2 succeeds, then the provider says attempt 1
    succeeded too. The booking stays CONFIRMED; the duplicate is logged for refund."""
    first = pay(client, user_headers, booking["id"], "FAILED").json()["provider_reference"]
    pay(client, user_headers, booking["id"], "SUCCESS")

    response = send_webhook(client, {"event_id": "evt_1", "payment_reference": first, "status": "SUCCESS"})
    assert response.status_code == 200
    assert booking_status(client, user_headers, booking["id"]) == "CONFIRMED"


def test_same_status_from_new_event_changes_nothing(client, db, booking, user_headers):
    reference = pay(client, user_headers, booking["id"], "SUCCESS").json()["provider_reference"]
    # A *different* event repeating the status we already have.
    response = send_webhook(client, {"event_id": "evt_new", "payment_reference": reference, "status": "SUCCESS"})
    assert response.json()["detail"] == "Event processed"
    assert booking_status(client, user_headers, booking["id"]) == "CONFIRMED"


def test_failing_twice_keeps_booking_failed(client, booking, user_headers):
    pay(client, user_headers, booking["id"], "FAILED")
    second = pay(client, user_headers, booking["id"], "FAILED")
    assert second.status_code == 201 and second.json()["status"] == "FAILED"
    assert booking_status(client, user_headers, booking["id"]) == "FAILED"


def test_random_outcome_without_simulate(client, booking, user_headers, monkeypatch):
    import app.services.payments as payments

    monkeypatch.setattr(payments.random, "random", lambda: 0.95)  # the unlucky 20%
    response = client.post("/payments/", headers=user_headers, json={"booking_id": booking["id"]})
    assert response.json()["status"] == "FAILED"

    monkeypatch.setattr(payments.random, "random", lambda: 0.10)
    response = client.post("/payments/", headers=user_headers, json={"booking_id": booking["id"]})
    assert response.json()["status"] == "SUCCESS"
    assert booking_status(client, user_headers, booking["id"]) == "CONFIRMED"
