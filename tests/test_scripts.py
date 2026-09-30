"""The seed script and the webhook helper script."""
import pytest
from sqlalchemy import func, select

import app.seed as seed
from app.models import CentreTest, DiagnosticCentre, DiagnosticTest, User
from app.security import verify_password
from scripts import send_webhook
from tests.conftest import TestingSession, engine, pay


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_seed_creates_data_and_is_safe_to_run_twice(db, monkeypatch, capsys):
    monkeypatch.setattr(seed, "engine", engine)
    monkeypatch.setattr(seed, "SessionLocal", TestingSession)

    seed.seed()
    seed.seed()  # second run must not duplicate anything

    assert count(db, DiagnosticCentre) == 2
    assert count(db, DiagnosticTest) == 3
    assert count(db, CentreTest) == 4
    admin = db.scalar(select(User).where(User.email == seed.ADMIN_EMAIL))
    assert admin.is_admin and verify_password(seed.ADMIN_PASSWORD, admin.hashed_password)
    assert count(db, User) == 1
    assert seed.ADMIN_EMAIL in capsys.readouterr().out


def test_send_webhook_signs_correctly(client, booking, user_headers):
    reference = pay(client, user_headers, booking["id"], "FAILED").json()["provider_reference"]
    event_id, response = send_webhook.send(client, reference, "SUCCESS", "evt_script")
    assert event_id == "evt_script"
    assert response.json()["detail"] == "Event processed"

    _, again = send_webhook.send(client, reference, "SUCCESS", "evt_script")
    assert again.json()["detail"] == "Event already processed"


def test_send_webhook_main(client, booking, user_headers, monkeypatch, capsys):
    reference = pay(client, user_headers, booking["id"], "FAILED").json()["provider_reference"]
    monkeypatch.setattr(send_webhook.httpx, "Client", lambda base_url: client)

    send_webhook.main(["send_webhook.py", reference, "SUCCESS"])  # no event id -> one is generated
    out = capsys.readouterr().out
    assert "event_id=evt_" in out and "Event processed" in out


def test_send_webhook_main_needs_arguments():
    with pytest.raises(SystemExit, match="usage"):
        send_webhook.main(["send_webhook.py"])
