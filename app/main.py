import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.config import settings
from app.database import Base, engine
from app.routers import auth, bookings, centres, payments

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s level=%(levelname)s logger=%(name)s %(message)s",
)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup. Fine for an assignment; a real project
    # would use migrations (Alembic) instead.
    Base.metadata.create_all(bind=engine)
    # The built-in secrets are public (they're in this repo). Anyone who knows
    # JWT_SECRET can create a valid login token for any user.
    for name in ("jwt_secret", "webhook_secret"):
        if "change-me" in getattr(settings, name):
            logger.warning("%s is a development default; set a real secret before deploying", name.upper())
    yield


app = FastAPI(
    title="EVE Diagnostics Booking API",
    description="Book diagnostic tests and pay for them (simulated payments).",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # A crash skips the normal log line below, so log it here.
        # FastAPI then answers 500 without leaking details to the client.
        logger.exception("method=%s path=%s status=500", request.method, request.url.path)
        raise
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "method=%s path=%s status=%s duration_ms=%.1f",
        request.method, request.url.path, response.status_code, duration_ms,
    )
    return response


app.include_router(auth.router)
app.include_router(centres.router)
app.include_router(bookings.router)
app.include_router(payments.router)


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
