"""App startup, health check, crash handling, database helpers."""
import logging

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

import app.main as main
import app.routers.centres as centres
from app.config import settings
from app.database import get_db, make_engine
from app.main import app


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_startup_creates_tables_and_warns_about_default_secrets(monkeypatch, caplog):
    fresh = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    monkeypatch.setattr(main, "engine", fresh)
    with caplog.at_level(logging.WARNING, logger="app"), TestClient(app):  # `with` runs startup
        pass
    assert {"users", "bookings", "payments", "webhook_events"} <= set(inspect(fresh).get_table_names())
    assert "JWT_SECRET is a development default" in caplog.text
    assert "WEBHOOK_SECRET is a development default" in caplog.text


def test_no_warning_when_real_secrets_are_set(monkeypatch, caplog):
    fresh = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    monkeypatch.setattr(main, "engine", fresh)
    monkeypatch.setattr(settings, "jwt_secret", "a-real-production-secret-of-good-length")
    monkeypatch.setattr(settings, "webhook_secret", "another-real-secret-value")
    with caplog.at_level(logging.WARNING, logger="app"), TestClient(app):
        pass
    assert "development default" not in caplog.text


def test_crash_returns_plain_500_and_is_logged(db, monkeypatch, caplog):
    def broken(*args):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(centres, "paginate", broken)
    with caplog.at_level(logging.INFO, logger="app"):
        response = TestClient(app, raise_server_exceptions=False).get("/centres/")
    assert response.status_code == 500
    assert response.text == "Internal Server Error"  # no internals leaked
    assert "secret internal detail" not in response.text
    assert "method=GET path=/centres/ status=500" in caplog.text


def test_make_engine_sqlite_and_postgres():
    assert make_engine("sqlite://").dialect.name == "sqlite"
    # Creating an engine doesn't connect yet, so no Postgres server is needed.
    assert make_engine("postgresql+psycopg2://u:p@localhost:5432/x").dialect.name == "postgresql"


def test_get_db_closes_the_session(monkeypatch):
    closed = []

    class FakeSession:
        def close(self):
            closed.append(True)

    monkeypatch.setattr("app.database.SessionLocal", FakeSession)
    gen = get_db()
    assert isinstance(next(gen), FakeSession)
    gen.close()  # FastAPI does this when the request ends
    assert closed == [True]
