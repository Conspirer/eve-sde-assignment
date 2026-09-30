import pytest

from app import rate_limit, redis_client

LOGIN = {"email": "user@example.com", "password": "password123"}


class FrozenClock:
    """Stand-in for the `time` module. Limits use 60-second windows, so with
    the real clock a test crossing a minute boundary would see its counts
    reset and fail at random. A frozen clock keeps the tests reliable."""

    def __init__(self, now: float):
        self.now = now

    def time(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    window_start = 1_700_000_000 - 1_700_000_000 % 60
    frozen = FrozenClock(window_start + 10)  # 10 seconds into a window
    monkeypatch.setattr(rate_limit, "time", frozen)
    return frozen


def login(client):
    return client.post("/auth/login/", json=LOGIN)


def test_login_is_limited_to_5_per_minute(client, user):
    for _ in range(5):
        assert login(client).status_code == 200

    blocked = login(client)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "50"  # 10s into the window -> 50s left


def test_limit_resets_in_the_next_window(client, user, clock):
    for _ in range(6):
        login(client)
    assert login(client).status_code == 429

    clock.now += 50  # the next minute starts
    assert login(client).status_code == 200


def test_failed_logins_count_too(client, user):
    for _ in range(5):
        client.post("/auth/login/", json={**LOGIN, "password": "wrong-password"})
    # Even the right password is refused now: that's what stops brute force.
    assert login(client).status_code == 429


def test_limits_are_separate_per_endpoint(client, user):
    for _ in range(6):
        login(client)
    signup = client.post("/auth/signup/", json={"email": "n@example.com", "full_name": "N", "password": "password123"})
    assert signup.status_code == 201


def test_rate_limit_uses_redis_when_available(client, user, fake_redis):
    for _ in range(5):
        assert login(client).status_code == 200
    assert login(client).status_code == 429
    assert list(fake_redis.scan_iter("ratelimit:login:*"))  # the counter lives in Redis


def test_rate_limit_still_works_if_redis_is_down(client, user, monkeypatch):
    dead = redis_client.make_client("redis://localhost:1/0")  # nothing listens on port 1
    monkeypatch.setattr(redis_client, "client", dead)
    for _ in range(5):
        assert login(client).status_code == 200
    assert login(client).status_code == 429  # fell back to in-memory counting
