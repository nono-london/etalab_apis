import pytest

from etalab_apis.sync_server.csv_geocoder import EtalabSyncCsvGeocoder


@pytest.mark.asyncio
async def test_geocode_live_smoke():
    """Live smoke test: 3-row roundtrip against POST /search/csv.

    Two known-good Paris addresses + one nonsense string to confirm the
    not-found path maps cleanly.
    """
    rows = [
        ("2 rue de la paix 75002 Paris", None),
        ("29 rue de la paix 75002 Paris", None),
        ("zzz nonsense address xxx", None),
    ]

    geocoder = EtalabSyncCsvGeocoder()
    results = []
    async for r in geocoder.geocode(iter(rows)):
        results.append(r)

    assert len(results) == 3
    found = [r for r in results if r["found_result"]]
    assert len(found) >= 2
    for r in found:
        assert r["city"] == "Paris"
        assert isinstance(r["lat"], float)
        assert isinstance(r["lng"], float)
        assert r["result_status"] == "ok"

    not_found = [r for r in results if not r["found_result"]]
    assert len(not_found) >= 1
    assert not_found[0]["result_status"] in {"not-found", "skipped"}


@pytest.mark.asyncio
async def test_geocode_with_insee_live_smoke():
    """Live smoke test: forward geocoding with INSEE filter."""
    rows = [
        ("2 rue de la paix", "75102"),
        ("29 rue de la paix", "75102"),
    ]

    geocoder = EtalabSyncCsvGeocoder()
    results = []
    async for r in geocoder.geocode(iter(rows)):
        results.append(r)

    assert len(results) == 2
    for r in results:
        assert r["found_result"], r
        assert r["city"] == "Paris"
        assert r["insee_city_code"] == "75102"


@pytest.mark.asyncio
async def test_reverse_geocode_live_smoke():
    """Live smoke test: reverse geocoding for a Paris coordinate."""
    rows = [(2.35222190, 48.85661400)]

    geocoder = EtalabSyncCsvGeocoder()
    results = []
    async for r in geocoder.reverse_geocode(iter(rows)):
        results.append(r)

    assert len(results) == 1
    r = results[0]
    assert r["found_result"], r
    assert r["city"] == "Paris"
