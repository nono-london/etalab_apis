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
        assert isinstance(r["result_score"], float)
        assert 0.0 <= r["result_score"] <= 1.0

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


@pytest.mark.asyncio
async def test_chunking_splits_input_into_multiple_chunks_live():
    """12 rows with chunk_rows=4 -> 3 server round trips. All results returned, order preserved."""
    rows = [("2 rue de la paix 75002 Paris", None)] * 12

    geocoder = EtalabSyncCsvGeocoder()
    results = []
    async for r in geocoder.geocode(iter(rows), chunk_rows=4):
        results.append(r)

    assert len(results) == 12
    for r in results:
        assert r["found_result"], r
        assert r["city"] == "Paris"


@pytest.mark.asyncio
async def test_geocode_accepts_generator_input_live():
    """Generator (not list) input works -- exercises the lazy itertools.islice path."""
    def addresses_gen():
        yield ("2 rue de la paix 75002 Paris", None)
        yield ("29 rue de la paix 75002 Paris", None)

    geocoder = EtalabSyncCsvGeocoder()
    results = []
    async for r in geocoder.geocode(addresses_gen()):
        results.append(r)

    assert len(results) == 2
    for r in results:
        assert r["found_result"], r


@pytest.mark.asyncio
async def test_geocode_empty_input_yields_no_results():
    """Empty iterable: no HTTP request, no results."""
    geocoder = EtalabSyncCsvGeocoder()
    results = []
    async for r in geocoder.geocode([]):
        results.append(r)
    assert results == []


@pytest.mark.asyncio
async def test_geocode_with_postcode_live_smoke():
    """3-tuple (addr, insee, postcode) — postcode column is sent and disambiguates the match."""
    rows = [
        ("2 rue de la paix", None, "75002"),
        ("29 rue de la paix", None, "75002"),
    ]

    geocoder = EtalabSyncCsvGeocoder()
    results = []
    async for r in geocoder.geocode(iter(rows)):
        results.append(r)

    assert len(results) == 2
    for r in results:
        assert r["found_result"], r
        assert r["city"] == "Paris"
        assert r["postcode"] == "75002"


@pytest.mark.asyncio
async def test_geocode_with_columns_passthrough_siret_live():
    """Arbitrary passthrough column (siret) is echoed verbatim on every result row,
    alongside the parsed geocoding fields.
    """
    rows = [
        {"siret": "12345678900001", "address": "2 rue de la paix 75002 Paris"},
        {"siret": "98765432100022", "address": "29 rue de la paix 75002 Paris"},
    ]

    geocoder = EtalabSyncCsvGeocoder()
    results = []
    async for r in geocoder.geocode_with_columns(iter(rows)):
        results.append(r)

    assert len(results) == 2
    assert results[0]["siret"] == "12345678900001"
    assert results[1]["siret"] == "98765432100022"
    for r in results:
        assert r["found_result"], r
        assert r["result_city"] == "Paris"
        assert r["result_postcode"] == "75002"
        assert isinstance(r["lat"], float)
        assert isinstance(r["lng"], float)
        assert 0.0 <= r["result_score"] <= 1.0


@pytest.mark.asyncio
async def test_geocode_with_columns_postcode_and_passthrough_live():
    """Passthrough siret + postcode filter column wired through together."""
    rows = [
        {"siret": "11111111100011", "addr": "2 rue de la paix", "pc": "75002"},
        {"siret": "22222222200022", "addr": "29 rue de la paix", "pc": "75002"},
    ]

    geocoder = EtalabSyncCsvGeocoder()
    results = []
    async for r in geocoder.geocode_with_columns(
        iter(rows), match_columns=("addr",), postcode_column="pc",
    ):
        results.append(r)

    assert len(results) == 2
    assert {r["siret"] for r in results} == {"11111111100011", "22222222200022"}
    for r in results:
        assert r["found_result"], r
        assert r["result_city"] == "Paris"
        assert r["result_postcode"] == "75002"
