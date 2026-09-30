SIGNUP = {"email": "new@example.com", "full_name": "New User", "password": "password123"}


def test_signup_creates_user(client):
    response = client.post("/auth/signup/", json=SIGNUP)
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@example.com"
    assert "password" not in body and "hashed_password" not in body


def test_signup_duplicate_email_is_rejected(client):
    client.post("/auth/signup/", json=SIGNUP)
    response = client.post("/auth/signup/", json={**SIGNUP, "email": "NEW@example.com"})
    assert response.status_code == 409


def test_signup_validates_input(client):
    assert client.post("/auth/signup/", json={**SIGNUP, "email": "not-an-email"}).status_code == 422
    assert client.post("/auth/signup/", json={**SIGNUP, "password": "short"}).status_code == 422
    assert client.post("/auth/signup/", json={"email": "a@b.com"}).status_code == 422


def test_login_returns_token_that_works(client):
    client.post("/auth/signup/", json=SIGNUP)
    response = client.post("/auth/login/", json={"email": SIGNUP["email"], "password": SIGNUP["password"]})
    assert response.status_code == 200
    token = response.json()["access_token"]

    me = client.get("/auth/me/", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == SIGNUP["email"]


def test_login_wrong_password(client):
    client.post("/auth/signup/", json=SIGNUP)
    response = client.post("/auth/login/", json={"email": SIGNUP["email"], "password": "wrong-password"})
    assert response.status_code == 401


def test_login_unknown_email(client):
    response = client.post("/auth/login/", json={"email": "nobody@example.com", "password": "password123"})
    assert response.status_code == 401


def test_protected_route_needs_token(client):
    assert client.get("/auth/me/").status_code == 401
    assert client.get("/auth/me/", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_password_too_long_in_bytes_is_rejected(client):
    # 40 x "é" = 40 characters but 80 bytes, over bcrypt's 72-byte limit
    response = client.post("/auth/signup/", json={**SIGNUP, "password": "é" * 40})
    assert response.status_code == 422


def test_login_with_very_long_password_is_401_not_500(client):
    client.post("/auth/signup/", json=SIGNUP)
    response = client.post("/auth/login/", json={"email": SIGNUP["email"], "password": "a" * 100})
    assert response.status_code == 401


def test_expired_token_rejected(client, user):
    import time

    import jwt

    from app.config import settings

    token = jwt.encode({"sub": str(user.id), "exp": int(time.time()) - 10}, settings.jwt_secret, algorithm="HS256")
    assert client.get("/auth/me/", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_seeded_admin_credentials_can_log_in(client, db):
    """The admin login printed in the README must pass email validation."""
    from app.models import User
    from app.security import hash_password
    from app.seed import ADMIN_EMAIL, ADMIN_PASSWORD

    db.add(User(email=ADMIN_EMAIL, full_name="Admin", hashed_password=hash_password(ADMIN_PASSWORD), is_admin=True))
    db.commit()
    response = client.post("/auth/login/", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert response.status_code == 200


def test_blank_full_name_rejected(client):
    assert client.post("/auth/signup/", json={**SIGNUP, "full_name": "   "}).status_code == 422


def test_token_of_deleted_user_rejected(client, db, user, user_headers):
    db.delete(user)
    db.commit()
    response = client.get("/auth/me/", headers=user_headers)
    assert response.status_code == 401
    assert response.json()["detail"] == "User no longer exists"
