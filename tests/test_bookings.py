from datetime import datetime, timedelta, timezone

from tests.conftest import future_time


def booking_body(centre_test, **overrides):
    return {"centre_id": centre_test.centre_id, "test_id": centre_test.test_id,
            "appointment_at": future_time(), **overrides}


def test_create_booking(booking):
    assert booking["status"] == "PENDING"
    assert booking["amount"] == "500.00"  # taken from the centre's price


def test_booking_requires_login(client, centre_test):
    assert client.post("/bookings/", json=booking_body(centre_test)).status_code == 401


def test_booking_in_the_past_rejected(client, user_headers, centre_test):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    response = client.post("/bookings/", headers=user_headers, json=booking_body(centre_test, appointment_at=past))
    assert response.status_code == 422


def test_booking_unknown_centre(client, user_headers, centre_test):
    response = client.post("/bookings/", headers=user_headers, json=booking_body(centre_test, centre_id=999))
    assert response.status_code == 404


def test_booking_test_not_offered_by_centre(client, user_headers, centre_test):
    response = client.post("/bookings/", headers=user_headers, json=booking_body(centre_test, test_id=999))
    assert response.status_code == 400


def test_duplicate_booking_rejected(client, user_headers, centre_test):
    body = booking_body(centre_test)
    assert client.post("/bookings/", headers=user_headers, json=body).status_code == 201
    assert client.post("/bookings/", headers=user_headers, json=body).status_code == 409


def test_user_sees_only_own_bookings(client, booking, user_headers, other_headers):
    assert client.get("/bookings/", headers=user_headers).json()["total"] == 1
    assert client.get("/bookings/", headers=other_headers).json()["total"] == 0


def test_other_user_cannot_view_or_cancel(client, booking, other_headers):
    assert client.get(f"/bookings/{booking['id']}", headers=other_headers).status_code == 404
    assert client.post(f"/bookings/{booking['id']}/cancel/", headers=other_headers).status_code == 404


def test_invalid_booking_id(client, user_headers):
    assert client.get("/bookings/999", headers=user_headers).status_code == 404
    assert client.get("/bookings/abc", headers=user_headers).status_code == 422


def test_cancel_booking(client, booking, user_headers):
    response = client.post(f"/bookings/{booking['id']}/cancel/", headers=user_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"

    # Cancelling twice is not allowed
    again = client.post(f"/bookings/{booking['id']}/cancel/", headers=user_headers)
    assert again.status_code == 409


def test_appointment_time_is_stored_in_utc(client, user_headers, centre_test):
    ist = timezone(timedelta(hours=5, minutes=30))
    local = (datetime.now(ist) + timedelta(days=10)).replace(hour=10, minute=0, second=0, microsecond=0)
    body = booking_body(centre_test, appointment_at=local.isoformat())  # e.g. ...T10:00:00+05:30
    response = client.post("/bookings/", headers=user_headers, json=body)
    assert response.json()["appointment_at"].startswith(local.astimezone(timezone.utc).strftime("%Y-%m-%dT04:30:00"))


def test_booking_more_than_a_year_ahead_rejected(client, user_headers, centre_test):
    body = booking_body(centre_test, appointment_at=future_time(days=400))
    assert client.post("/bookings/", headers=user_headers, json=body).status_code == 422


def test_out_of_range_ids_rejected_not_crash(client, user_headers, centre_test):
    # Bigger than any INTEGER id; SQLite used to crash (500) on these.
    huge = 10**20
    assert client.get(f"/bookings/{huge}", headers=user_headers).status_code == 422
    assert client.post(f"/bookings/{huge}/cancel/", headers=user_headers).status_code == 422
    assert client.get("/bookings/0", headers=user_headers).status_code == 422
    assert client.post("/bookings/", headers=user_headers, json=booking_body(centre_test, centre_id=huge)).status_code == 422
    assert client.post("/payments/", headers=user_headers, json={"booking_id": huge}).status_code == 422


def test_admin_sees_all_bookings(client, booking, other_headers, admin_headers, centre_test):
    client.post("/bookings/", headers=other_headers, json=booking_body(centre_test))
    assert client.get("/bookings/", headers=admin_headers).json()["total"] == 2


def test_filter_bookings_by_status(client, booking, user_headers):
    assert client.get("/bookings/?status_filter=PENDING", headers=user_headers).json()["total"] == 1
    assert client.get("/bookings/?status_filter=CONFIRMED", headers=user_headers).json()["total"] == 0
    assert client.get("/bookings/?status_filter=NOPE", headers=user_headers).status_code == 422


def test_time_without_timezone_is_treated_as_utc(client, user_headers, centre_test):
    naive = (datetime.now(timezone.utc) + timedelta(days=3)).replace(tzinfo=None, microsecond=0)
    response = client.post("/bookings/", headers=user_headers, json=booking_body(centre_test, appointment_at=naive.isoformat()))
    assert response.status_code == 201
    assert response.json()["appointment_at"].startswith(naive.isoformat())
