def test_list_centres_is_public_and_paginated(client, centre_test):
    response = client.get("/centres/?skip=0&limit=10")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "EVE Andheri"


def test_filter_centres_by_city(client, centre_test):
    assert client.get("/centres/?city=mumbai").json()["total"] == 1
    assert client.get("/centres/?city=Delhi").json()["total"] == 0


def test_invalid_pagination_rejected(client):
    assert client.get("/centres/?limit=0").status_code == 422
    assert client.get("/centres/?skip=-1").status_code == 422


def test_centre_detail_includes_tests_and_prices(client, centre_test):
    response = client.get(f"/centres/{centre_test.centre_id}")
    assert response.status_code == 200
    tests = response.json()["tests"]
    assert tests[0]["test"]["name"] == "Complete Blood Count"
    assert tests[0]["price"] == "500.00"


def test_centre_not_found(client):
    assert client.get("/centres/999").status_code == 404


def test_normal_user_cannot_create_centre(client, user_headers):
    response = client.post("/centres/", headers=user_headers,
                           json={"name": "X", "city": "Pune", "address": "Y"})
    assert response.status_code == 403


def test_admin_creates_centre_and_adds_test(client, admin_headers):
    centre = client.post("/centres/", headers=admin_headers,
                         json={"name": "EVE Pune", "city": "Pune", "address": "FC Road"})
    assert centre.status_code == 201
    test = client.post("/tests/", headers=admin_headers, json={"name": "Lipid Profile"})
    assert test.status_code == 201

    url = f"/centres/{centre.json()['id']}/tests/"
    added = client.post(url, headers=admin_headers, json={"test_id": test.json()["id"], "price": "799.00"})
    assert added.status_code == 201

    # Same test twice at the same centre -> conflict
    again = client.post(url, headers=admin_headers, json={"test_id": test.json()["id"], "price": "10.00"})
    assert again.status_code == 409


def test_price_must_be_positive(client, admin_headers, centre_test):
    url = f"/centres/{centre_test.centre_id}/tests/"
    for bad_price in ["-5", "0", "0.00", "1.234", "1e20", "abc"]:
        response = client.post(url, headers=admin_headers, json={"test_id": centre_test.test_id, "price": bad_price})
        assert response.status_code == 422, bad_price


def test_duplicate_test_name_rejected(client, admin_headers):
    client.post("/tests/", headers=admin_headers, json={"name": "CBC"})
    assert client.post("/tests/", headers=admin_headers, json={"name": "CBC"}).status_code == 409


def test_city_filter_is_not_a_wildcard(client, centre_test):
    assert client.get("/centres/?city=%25").json()["total"] == 0


def test_blank_names_rejected(client, admin_headers):
    response = client.post("/centres/", headers=admin_headers, json={"name": "   ", "city": "Pune", "address": "FC Road"})
    assert response.status_code == 422
    assert client.post("/tests/", headers=admin_headers, json={"name": "  "}).status_code == 422


def test_names_are_trimmed(client, admin_headers):
    response = client.post("/centres/", headers=admin_headers, json={"name": "  EVE Pune ", "city": "Pune", "address": "FC Road"})
    assert response.json()["name"] == "EVE Pune"


def test_huge_skip_and_long_city_rejected(client):
    assert client.get("/centres/?skip=99999999999999999999").status_code == 422
    assert client.get("/centres/?city=" + "a" * 101).status_code == 422


def test_out_of_range_centre_id_rejected(client):
    assert client.get(f"/centres/{10**20}").status_code == 422
    assert client.get("/centres/2147483647").status_code == 404  # largest valid id


def test_list_test_catalogue_is_public(client, centre_test):
    body = client.get("/tests/").json()
    assert body["total"] == 1 and body["items"][0]["name"] == "Complete Blood Count"


def test_add_test_to_unknown_centre_or_unknown_test(client, admin_headers, centre_test):
    assert client.post("/centres/999/tests/", headers=admin_headers,
                       json={"test_id": centre_test.test_id, "price": "5"}).status_code == 404
    assert client.post(f"/centres/{centre_test.centre_id}/tests/", headers=admin_headers,
                       json={"test_id": 999, "price": "5"}).status_code == 404
