from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.rate_limit import RateLimiter
from app.schemas import LoginRequest, SignupRequest, TokenResponse, UserOut
from app.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Slow down password guessing and mass sign-ups (per client IP).
login_limiter = RateLimiter("login", limit=5, window_seconds=60)
signup_limiter = RateLimiter("signup", limit=10, window_seconds=60)


@router.post(
    "/signup/",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(signup_limiter)],
)
def signup(data: SignupRequest, db: Session = Depends(get_db)):
    email = data.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email is already registered")

    user = User(email=email, full_name=data.full_name, hashed_password=hash_password(data.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Two signups with the same email at the same instant: the UNIQUE
        # constraint on email rejects the second one.
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email is already registered") from None
    return user


@router.post("/login/", response_model=TokenResponse, dependencies=[Depends(login_limiter)])
def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    # Same message for "no such user" and "wrong password", so attackers
    # can't use this endpoint to find out which emails are registered.
    if user is None or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me/", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
