import pytest
from fastapi.testclient import TestClient

import api


@pytest.fixture
def client(monkeypatch, sample_db_path):
    monkeypatch.setattr(api, "DB_PATH", sample_db_path)
    monkeypatch.setattr(api, "_taxonomy_cache", None)
    # Never make real calls to the eBird API from tests.
    monkeypatch.setattr(api, "fetch_recent_species", lambda locality_id: set())
    return TestClient(api.app)


# --- /api/optimize --------------------------------------------------------
# Same fixture data as test_hotspot_optimizer.py: hotspots 1-3, day_of_year=100,
# greedy order [1, 2, 3] with gains [1.4, 0.83, 0.34].


def test_optimize_happy_path(client):
    resp = client.post(
        "/api/optimize",
        json={"life_list": [], "start_date": "2023-04-10", "end_date": "2023-04-10", "k": 3},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [h["locality_id"] for h in body["hotspots"]] == [1, 2, 3]
    assert body["hotspots"][0]["marginal_gain"] == pytest.approx(1.4)
    assert body["num_candidate_hotspots"] == 3
    assert body["geographic_filter"] == "All areas"


def test_optimize_missing_required_field_is_422(client):
    resp = client.post("/api/optimize", json={"life_list": [], "start_date": "2023-04-10"})
    assert resp.status_code == 422


def test_optimize_invalid_date_is_422(client):
    resp = client.post(
        "/api/optimize",
        json={"life_list": [], "start_date": "not-a-date", "end_date": "2023-04-10"},
    )
    assert resp.status_code == 422


def test_optimize_negative_k_returns_empty_selection_not_error(client):
    # No explicit validation on k today - document current behavior rather
    # than assume it: negative k degrades to an empty selection, not a crash.
    resp = client.post(
        "/api/optimize",
        json={
            "life_list": [], "start_date": "2023-04-10", "end_date": "2023-04-10", "k": -1,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["hotspots"] == []


def test_optimize_no_matching_data_returns_empty_result(client):
    resp = client.post(
        "/api/optimize",
        json={"life_list": [], "start_date": "2023-01-01", "end_date": "2023-01-01", "k": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["hotspots"] == []
    assert body["total_expected_lifers"] == 0


def test_optimize_state_counties_filter(client):
    resp = client.post(
        "/api/optimize",
        json={
            "life_list": [], "start_date": "2023-04-10", "end_date": "2023-04-10", "k": 5,
            "state_counties": [{"state": "NH", "county": "County2"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [h["locality_id"] for h in body["hotspots"]] == [3]
    assert body["geographic_filter"] == "County2"


def test_optimize_driving_filter_restricts_to_returned_ids(client, monkeypatch):
    monkeypatch.setattr(api, "osrm_filter_hotspots", lambda lat, lon, mins: [2])
    resp = client.post(
        "/api/optimize",
        json={
            "life_list": [], "start_date": "2023-04-10", "end_date": "2023-04-10", "k": 5,
            "center_lat": 41.0, "center_lon": -71.0, "max_driving_minutes": 30,
        },
    )
    assert resp.status_code == 200
    assert [h["locality_id"] for h in resp.json()["hotspots"]] == [2]


def test_optimize_driving_filter_no_candidates_short_circuits(client, monkeypatch):
    monkeypatch.setattr(api, "osrm_filter_hotspots", lambda lat, lon, mins: [])
    resp = client.post(
        "/api/optimize",
        json={
            "life_list": [], "start_date": "2023-04-10", "end_date": "2023-04-10", "k": 5,
            "center_lat": 41.0, "center_lon": -71.0, "max_driving_minutes": 30,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["hotspots"] == []
    assert body["geographic_filter"] == "Within 30 min drive"


def test_optimize_partial_driving_filter_fields_are_ignored(client, monkeypatch):
    # All three of center_lat/center_lon/max_driving_minutes are required to
    # activate the driving filter - partial input should just be ignored,
    # not raise or silently misbehave.
    called = False

    def fail_if_called(*a, **kw):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(api, "osrm_filter_hotspots", fail_if_called)
    resp = client.post(
        "/api/optimize",
        json={
            "life_list": [], "start_date": "2023-04-10", "end_date": "2023-04-10", "k": 5,
            "center_lat": 41.0,
        },
    )
    assert resp.status_code == 200
    assert not called
    assert [h["locality_id"] for h in resp.json()["hotspots"]] == [1, 2, 3]


# --- /api/search-hotspots --------------------------------------------------


def test_search_hotspots_query_too_short_returns_empty(client):
    resp = client.get("/api/search-hotspots", params={"q": "H"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_search_hotspots_matches_by_substring(client):
    resp = client.get("/api/search-hotspots", params={"q": "Hotspot"})
    assert resp.status_code == 200
    names = [r["locality"] for r in resp.json()]
    assert names == ["Hotspot A", "Hotspot B", "Hotspot C"]


def test_search_hotspots_filters_by_state(client):
    resp = client.get("/api/search-hotspots", params={"q": "Hotspot", "states": "NH"})
    assert resp.status_code == 200
    assert [r["locality_id"] for r in resp.json()] == [3]


def test_search_hotspots_filters_by_state_counties(client):
    resp = client.get(
        "/api/search-hotspots", params={"q": "Hotspot", "state_counties": "VT|County1"}
    )
    assert resp.status_code == 200
    assert sorted(r["locality_id"] for r in resp.json()) == [1, 2]


def test_search_hotspots_malformed_state_counties_pair_is_skipped(client):
    # Missing the "|" delimiter: the pair is dropped, `pairs` ends up empty,
    # and the code falls back to its "1=1" default - i.e. a garbled filter
    # silently behaves as *no* geo filter, not as "no results". Documenting
    # this as current behavior rather than assuming it's "obviously" one or
    # the other.
    resp = client.get(
        "/api/search-hotspots", params={"q": "Hotspot", "state_counties": "VT-County1"}
    )
    assert resp.status_code == 200
    assert sorted(r["locality_id"] for r in resp.json()) == [1, 2, 3]


# --- /api/hotspot-details --------------------------------------------------


def test_hotspot_details_happy_path(client):
    resp = client.post(
        "/api/hotspot-details",
        json={"locality_id": 1, "start_date": "2023-04-10", "end_date": "2023-04-10"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["locality_id"] == 1
    # Sorted descending by probability: American Robin (0.9) before Blue Jay (0.5).
    assert [s["common_name"] for s in body["target_species"]] == ["American Robin", "Blue Jay"]


def test_hotspot_details_unknown_locality_id_is_404(client):
    resp = client.post(
        "/api/hotspot-details",
        json={"locality_id": 999, "start_date": "2023-04-10", "end_date": "2023-04-10"},
    )
    assert resp.status_code == 404


def test_hotspot_details_non_integer_locality_id_is_422(client):
    resp = client.post(
        "/api/hotspot-details",
        json={"locality_id": "not-an-id", "start_date": "2023-04-10", "end_date": "2023-04-10"},
    )
    assert resp.status_code == 422


def test_hotspot_details_no_matching_data_returns_basic_info(client):
    resp = client.post(
        "/api/hotspot-details",
        json={"locality_id": 1, "start_date": "2023-01-01", "end_date": "2023-01-01"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["locality_id"] == 1
    assert body["target_species"] == []


# --- /api/region-names -----------------------------------------------------
# data/subnational1.csv isn't tracked in git (data/ is gitignored), so a
# fresh checkout/CI environment has no copy of it - this endpoint needs
# BASE_DIR redirected to a fixture directory that has one.


def test_region_names_maps_country_and_subnational_codes(client, monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "subnational1.csv").write_text(
        "country_code,country_name,subnational1_code,subnational1_name\n"
        "US,United States,US-VT,Vermont\n"
        "US,United States,US-NH,New Hampshire\n"
    )
    monkeypatch.setattr(api, "BASE_DIR", tmp_path)

    resp = client.get("/api/region-names")
    assert resp.status_code == 200
    body = resp.json()
    assert body["US"] == "United States"
    assert body["US-VT"] == "Vermont"
    assert body["US-NH"] == "New Hampshire"


# --- /api/search-areas, /api/species ---------------------------------------


def test_search_areas_shape(client):
    resp = client.get("/api/search-areas")
    assert resp.status_code == 200
    body = resp.json()
    # DISTINCT collapses hotspots 1 and 2, which share (US, VT, County1).
    assert body["US"]["VT"] == ["County1"]
    assert body["US"]["NH"] == ["County2"]


def test_species_excludes_non_species_categories(client):
    resp = client.get("/api/species")
    assert resp.status_code == 200
    body = resp.json()
    assert [row["comName"] for row in body] == ["American Robin", "Blue Jay", "Cedar Waxwing"]


def test_species_is_cached_across_calls(client):
    first = client.get("/api/species").json()
    second = client.get("/api/species").json()
    assert first == second
    assert api._taxonomy_cache is not None
