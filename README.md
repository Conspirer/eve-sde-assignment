# EVE Diagnostics Booking API

A small backend for booking diagnostic tests (blood tests, thyroid tests and so on) at diagnostic centres, and paying for them through a **simulated** payment provider.

**Stack:** Python 3.12 · FastAPI · SQLAlchemy 2 · PostgreSQL (SQLite for quick local runs and tests) · Redis (optional) · JWT · pytest · Docker

---

## 1. How to run

### Option A: Docker (recommended, uses PostgreSQL)

```bash
docker compose up --build
```

- API: http://localhost:8000
- Interactive docs (Swagger): http://localhost:8000/docs
- Starts PostgreSQL, Redis and the API.
- Sample data is loaded automatically. Admin login: `admin@example.com` / `admin12345`

### Option B: Local Python (uses SQLite, no database setup needed)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m app.seed               # sample centres, tests and an admin user
uvicorn app.main:app --reload
```

To use PostgreSQL or Redis locally, copy `.env.example` to `.env` and set `DATABASE_URL` / `REDIS_URL`.
Redis is optional: without it, caching is turned off and rate limits are counted in memory.

### Run the tests

```bash
pytest -q
pytest -q --cov        # with a coverage report: 100% of lines and branches
```

Each test runs against a fresh in-memory SQLite database, so no setup is needed.
To run the same tests on PostgreSQL (which, unlike SQLite, enforces the row locks), run them inside Docker:

```bash
docker compose run --rm -e TEST_DATABASE_URL=postgresql+psycopg2://eve:eve@db:5432/eve_test api pytest -q -p no:cacheprovider
```

This uses a separate `eve_test` database, because the tests delete every table when they finish. The test setup refuses any database whose name doesn't end in `_test`.

---

## 2. API endpoints

All request and response bodies are JSON. Protected endpoints need the header `Authorization: Bearer <token>`.

| Method | Path | Who | What it does |
|---|---|---|---|
| POST | `/auth/signup/` | anyone | Create an account |
| POST | `/auth/login/` | anyone | Get a JWT token |
| GET | `/auth/me/` | user | Current user |
| GET | `/centres/?city=&skip=&limit=` | anyone | List centres (paginated, filter by city) |
| GET | `/centres/{id}` | anyone | Centre details, with its tests and prices |
| POST | `/centres/` | admin | Create a centre |
| POST | `/centres/{id}/tests/` | admin | Add a test to a centre, with a price |
| GET | `/tests/` | anyone | Test catalogue |
| POST | `/tests/` | admin | Add a test to the catalogue |
| POST | `/bookings/` | user | Book a test (status starts as `PENDING`) |
| GET | `/bookings/?status_filter=&skip=&limit=` | user | My bookings (admins see all) |
| GET | `/bookings/{id}` | owner/admin | One booking |
| POST | `/bookings/{id}/cancel/` | owner/admin | Cancel a booking |
| POST | `/payments/` | owner/admin | Pay for a booking (simulated) |
| POST | `/payments/webhook/` | payment provider | Payment status update (signed, idempotent) |
| GET | `/health` | anyone | Health check |

### Example walkthrough

```bash
# 1. Sign up and log in
curl -X POST localhost:8000/auth/signup/ -H 'Content-Type: application/json' \
  -d '{"email":"riya@example.com","full_name":"Riya","password":"password123"}'

curl -X POST localhost:8000/auth/login/ -H 'Content-Type: application/json' \
  -d '{"email":"riya@example.com","password":"password123"}'
# -> {"access_token":"eyJ...","token_type":"bearer"}
TOKEN=eyJ...

# 2. Browse centres
curl "localhost:8000/centres/?city=mumbai"
curl localhost:8000/centres/1

# 3. Book a test. appointment_at must be in the future and within 365 days,
#    so change the date below if it has already passed.
curl -X POST localhost:8000/bookings/ -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"centre_id":1,"test_id":1,"appointment_at":"2026-12-01T10:00:00+05:30"}'
# -> {"id":1,"amount":"399.00","status":"PENDING",...}

# 4. Pay. "simulate_outcome" is optional; leave it out for a random result.
curl -X POST localhost:8000/payments/ -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"booking_id":1,"simulate_outcome":"SUCCESS"}'
# -> {"id":1,"status":"SUCCESS","provider_reference":"pay_3f2a...",...}
#    The booking is now CONFIRMED.

# 5. Act as the payment provider and send a signed webhook.
#    Run it twice with the same event id: the second call changes nothing.
python scripts/send_webhook.py pay_3f2a... SUCCESS evt_123
python scripts/send_webhook.py pay_3f2a... SUCCESS evt_123
# -> 200 {'detail': 'Event processed'}
# -> 200 {'detail': 'Event already processed'}
#
# Using Docker only? Run the script inside the api container instead:
#   docker compose exec api python scripts/send_webhook.py pay_3f2a... SUCCESS evt_123
```

### Webhook format

```http
POST /payments/webhook/
X-Webhook-Signature: <hex HMAC-SHA256 of the raw body using WEBHOOK_SECRET>

{"event_id": "evt_123", "payment_reference": "pay_3f2a...", "status": "SUCCESS"}
```

| Situation | Response |
|---|---|
| Missing or wrong signature | `401` |
| Invalid body | `422` |
| Unknown `payment_reference` | `404` |
| New event | `200 {"detail": "Event processed"}` |
| Event already seen | `200 {"detail": "Event already processed"}` (no changes made) |

### Error codes used

| Code | Meaning |
|---|---|
| 400 | The request is valid but makes no sense, e.g. the centre doesn't offer that test |
| 401 | Not logged in, bad token, or bad webhook signature |
| 403 | Logged in but not allowed (e.g. a non-admin creating a centre) |
| 404 | Not found, **or it belongs to another user** (so booking ids can't be guessed) |
| 409 | Conflict with the current state: duplicate email, paying a confirmed booking, cancelling twice |
| 422 | Validation failed: bad email, short password, past date, negative price |
| 429 | Too many requests (rate limit). The `Retry-After` header says how many seconds to wait |

---

## 3. Database design

```
users ─────────────< bookings >──────────── diagnostic_centres
  │                     │  │                        │
  │                     │  └──── diagnostic_tests   └──< centre_tests >── diagnostic_tests
  │                     │                                 (price lives here)
  │                     └──< payments
  │
webhook_events  (log of processed webhook events, for idempotency)
```

| Table | Key columns | Notes |
|---|---|---|
| `users` | id, email (unique), full_name, hashed_password, is_admin | Only the bcrypt hash of the password is stored |
| `diagnostic_centres` | id, name, city (indexed), address | |
| `diagnostic_tests` | id, name (unique), description | Global catalogue with no price |
| `centre_tests` | centre_id, test_id, price | **UNIQUE(centre_id, test_id)**, **CHECK(price > 0)**. The same test can have a different price at each centre |
| `bookings` | user_id, centre_id, test_id, appointment_at, amount, status | `amount` is a *copy* of the price at booking time |
| `payments` | booking_id, amount, status, provider_reference (unique) | One row per payment **attempt**, so retries are kept |
| `webhook_events` | event_id (**unique**), payment_reference, status | The unique constraint is what guarantees idempotency |

Choices worth pointing out:
- **Money uses `NUMERIC(10,2)`, never float.** Floats can't store values like 0.1 exactly.
- **Timestamps are timezone-aware and stored in UTC.**
- **Booking and payment statuses are enums**, so the database rejects unknown values.

### Booking lifecycle

```
PENDING ──payment ok──▶ CONFIRMED ──cancel──▶ CANCELLED
   │                        ▲
   └──payment fails──▶ FAILED ──retry ok
PENDING / FAILED ──cancel──▶ CANCELLED       CANCELLED is final
```

All allowed transitions are defined in one place: [`app/services/booking_state.py`](app/services/booking_state.py).

---

## 4. How the tricky parts work

**Idempotent webhook** ([`app/routers/payments.py`](app/routers/payments.py))
1. Verify the HMAC signature, so only the real provider can call the webhook.
2. If `event_id` is already in `webhook_events`, return 200 and do nothing.
3. Otherwise, in **one transaction**, save the event and update the payment and booking.
4. If two identical events arrive at the same instant, both pass step 2, but the database's `UNIQUE(event_id)` constraint lets only one insert succeed. The other gets an `IntegrityError`, is rolled back, and returns "already processed". I tested this against Postgres with 10 identical concurrent requests: exactly one was processed.

**Rules for payment results** ([`app/services/payments.py`](app/services/payments.py)), used by both `/payments/` and the webhook:
- Receiving the same status again does nothing.
- A `SUCCESS` payment is never changed back, because undoing it would need a refund flow.
- A late `SUCCESS` for a cancelled booking leaves the booking `CANCELLED` and logs a warning saying a refund is needed.

**Double-payment protection:** `POST /payments/` locks the booking row (`SELECT ... FOR UPDATE`). If a second payment request comes in at the same moment, it waits, then sees `CONFIRMED` and gets `409`. The webhook locks the payment and the booking too. If two different attempts both end up `SUCCESS`, the booking stays `CONFIRMED` and the extra one is logged as needing a refund.

**Caching (Redis)** ([`app/cache.py`](app/cache.py), [`app/routers/centres.py`](app/routers/centres.py))
- `GET /centres/` and `GET /centres/{id}` are cached in Redis for 5 minutes, because centres are read far more often than they change. The response header `X-Cache: HIT` or `MISS` shows whether a request came from the cache.
- Each filter and page combination has its own key, e.g. `centres:list:city=mumbai:skip=0:limit=20`.
- **Invalidation:** when an admin creates a centre or adds a test to one, every `centres:*` key is deleted (using `SCAN`, not `KEYS`, which would block Redis). The next read rebuilds the cache from the database.
- **If Redis is down, the API keeps working.** Every cache call catches Redis errors and simply reads from the database. Retries are turned off and timeouts are short, so a dead Redis costs only a few milliseconds per request. I tested this by stopping the Redis container while the API was running.
- Trade-off: if an admin changes something *while* Redis is down, the cache can't be cleared. Once Redis is back it could serve old data for up to the 5-minute TTL. The TTL is the safety net that limits this.

**Rate limiting** ([`app/rate_limit.py`](app/rate_limit.py))
- Login: 5 requests per minute per IP. Signup: 10 per minute per IP. Going over returns `429` with a `Retry-After` header.
- Algorithm: a fixed window. Redis keeps a counter per IP per minute (`INCR` + `EXPIRE`), so all API servers share the same counts.
- Failed logins count too. That's what makes password guessing slow.
- Without Redis, the counts are kept in memory. That's fine for one server, but each server would have its own counts.

---

## 5. Assumptions

- Payment is simulated. `POST /payments/` returns a result immediately, **and** the provider may also send a webhook later, as real gateways do. The webhook is treated as the source of truth, so it can turn a `FAILED` payment into `SUCCESS` (for example, when a slow bank confirms late).
- `POST /payments/` returns `201` even when the payment fails. A payment attempt *was* created, and its `status` says how it went.
- A booking can have several payment attempts. It can be paid for only while it is `PENDING` or `FAILED`.
- Centres and tests are managed by admins. Admins are created through the seed script, not through signup.
- "Manage" means creating centres, creating tests and adding tests to centres at a price. Editing and deleting were left out to keep the scope small.
- Appointments must be in the future and at most 365 days ahead.
- Text fields are trimmed, so a name made only of spaces is rejected.
- IDs must be between 1 and 2,147,483,647 (the largest value an INTEGER column holds). Anything outside that range returns 422.
- Anyone can browse centres without logging in.
- Cancelling a `CONFIRMED` booking is allowed. Refunds are out of scope.
- Tables are created on startup with `create_all`. There are no migrations.
- The built-in secrets (`JWT_SECRET`, `WEBHOOK_SECRET`), the database password and the seeded admin password are for local development only. The app logs a warning at startup while the default secrets are in use.
- Rate limits are per client IP. Behind a proxy or load balancer the real IP comes from `X-Forwarded-For`, which the proxy must set; that isn't configured here.

## 6. What I would improve with more time

- **Alembic migrations** instead of `create_all`
- **Refund flow** for cancelled paid bookings and late successful payments
- **Async webhook processing** with Celery or RQ: save the event, return 200 immediately, and process it in a worker with retries
- **Sliding-window rate limits**, and per-account limits (not only per IP), so an attacker can't get around the limit by using many IPs
- A **circuit breaker** for Redis: after a failure, skip Redis for a few seconds instead of trying it on every request
- **Constant-time login**: today a login for an unknown email returns slightly faster (no bcrypt check), which in theory reveals whether an email is registered
- **Time slots and capacity** per centre, so two people can't take the same slot
- **Refresh tokens** and logout (token revocation)
- An **idempotency key** on `POST /payments/` so client retries are safe
- JSON logs with request ids, plus metrics

## Project layout

```
app/
  main.py              # app setup, logging middleware, routers
  config.py            # settings from environment variables
  database.py          # engine, session, get_db dependency
  models.py            # database tables
  schemas.py           # request/response validation (Pydantic)
  security.py          # password hashing, JWT, webhook signatures
  redis_client.py      # Redis connection (optional)
  cache.py             # JSON cache that keeps working when Redis is down
  rate_limit.py        # per-IP rate limiter (Redis, or memory)
  pagination.py
  seed.py              # sample data
  routers/             # HTTP layer: auth, centres, bookings, payments
  services/            # business rules: booking_state, payments
tests/                 # 96 tests (pytest), 100% line + branch coverage
scripts/send_webhook.py  # pretend to be the payment provider
docker/init-test-db.sql  # creates the eve_test database in Docker
```
