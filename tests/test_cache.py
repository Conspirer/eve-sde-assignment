from app import redis_client


def test_centre_list_is_cached(client, centre_test, fake_redis):
    first = client.get("/centres/")
    second = client.get("/centres/")
    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"
    assert first.json() == second.json()


def test_centre_detail_is_cached(client, centre_test, fake_redis):
    url = f"/centres/{centre_test.centre_id}"
    assert client.get(url).headers["X-Cache"] == "MISS"
    hit = client.get(url)
    assert hit.headers["X-Cache"] == "HIT"
    assert hit.json()["tests"][0]["price"] == "500.00"


def test_different_filters_are_cached_separately(client, centre_test, fake_redis):
    client.get("/centres/?city=Mumbai")
    assert client.get("/centres/?city=Delhi").headers["X-Cache"] == "MISS"
    assert client.get("/centres/?city=delhi").headers["X-Cache"] == "HIT"  # same filter, any case


def test_creating_a_centre_clears_the_cache(client, centre_test, admin_headers, fake_redis):
    client.get("/centres/")  # now cached, total = 1
    client.post("/centres/", headers=admin_headers, json={"name": "EVE Pune", "city": "Pune", "address": "FC Road"})

    fresh = client.get("/centres/")
    assert fresh.headers["X-Cache"] == "MISS"
    assert fresh.json()["total"] == 2


def test_adding_a_test_clears_the_centre_detail(client, centre_test, admin_headers, fake_redis):
    url = f"/centres/{centre_test.centre_id}"
    client.get(url)  # cached with 1 test
    new_test = client.post("/tests/", headers=admin_headers, json={"name": "Lipid Profile"}).json()
    client.post(f"{url}/tests/", headers=admin_headers, json={"test_id": new_test["id"], "price": "799.00"})

    fresh = client.get(url)
    assert fresh.headers["X-Cache"] == "MISS"
    assert len(fresh.json()["tests"]) == 2


def test_not_found_is_not_cached(client, fake_redis):
    assert client.get("/centres/999").status_code == 404
    assert not list(fake_redis.scan_iter("centres:*"))


def test_api_works_when_redis_is_down(client, centre_test, monkeypatch):
    dead = redis_client.make_client("redis://localhost:1/0")  # nothing listens on port 1
    monkeypatch.setattr(redis_client, "client", dead)
    response = client.get("/centres/")
    assert response.status_code == 200
    assert response.headers["X-Cache"] == "MISS"
    assert response.json()["total"] == 1


def test_write_with_nothing_cached_is_fine(client, admin_headers, fake_redis):
    response = client.post("/centres/", headers=admin_headers, json={"name": "X", "city": "Pune", "address": "Y"})
    assert response.status_code == 201


def test_admin_write_works_when_redis_is_down(client, admin_headers, monkeypatch):
    monkeypatch.setattr(redis_client, "client", redis_client.make_client("redis://localhost:1/0"))
    response = client.post("/centres/", headers=admin_headers, json={"name": "X", "city": "Pune", "address": "Y"})
    assert response.status_code == 201  # clearing the cache failed, but the write succeeded
