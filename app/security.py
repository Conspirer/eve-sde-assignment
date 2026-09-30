"""Passwords, JWT tokens, "who is calling?" dependencies, webhook signatures."""
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User

# ---------- Passwords ----------
# We never store the password itself, only a bcrypt hash of it.
# bcrypt is slow on purpose and adds a random "salt", so leaked hashes
# are very hard to reverse.

BCRYPT_MAX_BYTES = 72  # bcrypt refuses longer input


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    # Signup never accepts passwords this long, so it can't be correct.
    # Checking here stops bcrypt raising an error (-> 500) on long input.
    if len(password.encode()) > BCRYPT_MAX_BYTES:
        return False
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ---------- JWT ----------
# A JWT is a signed string holding {"sub": user_id, "exp": expiry}.
# Anyone can read it, but only we (with JWT_SECRET) can create a valid one,
# so if the signature checks out we can trust the user id inside it.

def create_access_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Dependency: reads `Authorization: Bearer <token>` and returns the user."""
    if credentials is None:
        raise _unauthorized("Not authenticated")
    try:
        payload = jwt.decode(
            credentials.credentials, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _unauthorized("Invalid or expired token") from None

    user = db.get(User, user_id)
    if user is None:
        raise _unauthorized("User no longer exists")
    return user


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    """Dependency: like get_current_user, but only lets admins through."""
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


# ---------- Webhook signatures ----------
# The webhook URL is public, so anyone could call it and "confirm" a booking.
# The provider signs each request body with a shared secret (HMAC-SHA256);
# we recompute the signature and reject the call if it doesn't match.

def sign_webhook_body(body: bytes) -> str:
    return hmac.new(settings.webhook_secret.encode(), body, hashlib.sha256).hexdigest()


def is_valid_webhook_signature(body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    # compare_digest takes the same time whether the strings match or not,
    # so attackers can't guess the signature one character at a time.
    return hmac.compare_digest(sign_webhook_body(body), signature)
