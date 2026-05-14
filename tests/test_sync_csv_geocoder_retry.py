"""Mocked-HTTP tests for the retry/backoff/subdivide paths in EtalabSyncCsvGeocoder.

These paths cannot be triggered against the live API (we cannot make the server
return 429 or 5xx on demand without abusing it). Everything else is covered by
the live smoke tests in test_sync_csv_geocoder.py.
"""

from unittest.mock import AsyncMock

import aiohttp
import pytest
from aioresponses import aioresponses

from etalab_apis.sync_server.csv_geocoder import EtalabSyncCsvGeocoder

SEARCH_CSV_URL = "https://data.geopf.fr/geocodage/search/csv"

OK_HEADER = (
    "address,result_status,result_label,result_city,"
    "result_postcode,result_citycode,result_score,result_score_next,"
    "latitude,longitude"
)


def _ok_body(addresses: list[str]) -> str:
    lines = [OK_HEADER]
    for addr in addresses:
        lines.append(
            f"{addr},ok,{addr} (matched),Paris,75002,75102,0.95,0.42,48.86,2.33"
        )
    return "\n".join(lines) + "\n"


@pytest.fixture
def fake_sleep(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(
        "etalab_apis.sync_server.csv_geocoder.asyncio.sleep",
        mock,
    )
    return mock


@pytest.mark.asyncio
async def test_429_retry_honors_retry_after(fake_sleep):
    """Server says retry-after: 3 -> we wait exactly 3s then retry once."""
    with aioresponses() as m:
        m.post(SEARCH_CSV_URL, status=429, headers={"retry-after": "3"})
        m.post(SEARCH_CSV_URL, status=200, body=_ok_body(["2 rue paix"]))

        geocoder = EtalabSyncCsvGeocoder()
        results = [r async for r in geocoder.geocode([("2 rue paix", None)])]

    assert len(results) == 1
    assert results[0]["found_result"]
    fake_sleep.assert_called_once_with(3.0)


@pytest.mark.asyncio
async def test_5xx_exponential_backoff_then_success(fake_sleep):
    """3 consecutive 5xx -> exponential backoff 1s, 2s, 4s -> 4th attempt succeeds."""
    with aioresponses() as m:
        m.post(SEARCH_CSV_URL, status=500, body="boom")
        m.post(SEARCH_CSV_URL, status=502, body="bad gateway")
        m.post(SEARCH_CSV_URL, status=503, body="unavailable")
        m.post(SEARCH_CSV_URL, status=200, body=_ok_body(["2 rue paix"]))

        geocoder = EtalabSyncCsvGeocoder()
        results = [r async for r in geocoder.geocode([("2 rue paix", None)])]

    assert len(results) == 1
    assert results[0]["found_result"]
    waits = [c.args[0] for c in fake_sleep.call_args_list]
    assert waits == [1, 2, 4]


@pytest.mark.asyncio
async def test_subdivide_on_persistent_failure_preserves_order(fake_sleep):
    """200-row chunk hits 5x 500 -> halved 100/100 -> each succeeds. Order preserved."""
    rows = [(f"addr_{i}", None) for i in range(200)]
    first_half = [f"addr_{i}" for i in range(100)]
    second_half = [f"addr_{i}" for i in range(100, 200)]

    with aioresponses() as m:
        for _ in range(5):
            m.post(SEARCH_CSV_URL, status=500, body="boom")
        m.post(SEARCH_CSV_URL, status=200, body=_ok_body(first_half))
        m.post(SEARCH_CSV_URL, status=200, body=_ok_body(second_half))

        geocoder = EtalabSyncCsvGeocoder(min_subdivide_rows=50)
        results = [r async for r in geocoder.geocode(rows)]

    assert len(results) == 200
    assert results[0]["postal_address"] == "addr_0 (matched)"
    assert results[99]["postal_address"] == "addr_99 (matched)"
    assert results[100]["postal_address"] == "addr_100 (matched)"
    assert results[199]["postal_address"] == "addr_199 (matched)"


@pytest.mark.asyncio
async def test_truncated_200_retried_then_success_tuple_path(fake_sleep, monkeypatch):
    """Server returns 200 but the body is truncated: resp.text() raises ClientPayloadError.
    Retry on the tuple-input path with exponential backoff; 3rd attempt succeeds.
    """
    real_text = aiohttp.ClientResponse.text
    call_count = {"n": 0}

    async def flaky_text(self):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise aiohttp.ClientPayloadError(
                "Not enough data to satisfy transfer length header."
            )
        return await real_text(self)

    monkeypatch.setattr(aiohttp.ClientResponse, "text", flaky_text)

    with aioresponses() as m:
        for _ in range(3):
            m.post(SEARCH_CSV_URL, status=200, body=_ok_body(["2 rue paix"]))

        geocoder = EtalabSyncCsvGeocoder()
        results = [r async for r in geocoder.geocode([("2 rue paix", None)])]

    assert len(results) == 1
    assert results[0]["found_result"]
    waits = [c.args[0] for c in fake_sleep.call_args_list]
    assert waits == [1, 2]


@pytest.mark.asyncio
async def test_truncated_200_retried_then_success_dict_path(fake_sleep, monkeypatch):
    """Same as above but via geocode_with_columns (dict input path)."""
    real_text = aiohttp.ClientResponse.text
    call_count = {"n": 0}

    async def flaky_text(self):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise aiohttp.ClientPayloadError(
                "Not enough data to satisfy transfer length header."
            )
        return await real_text(self)

    monkeypatch.setattr(aiohttp.ClientResponse, "text", flaky_text)

    with aioresponses() as m:
        for _ in range(3):
            m.post(SEARCH_CSV_URL, status=200, body=_ok_body(["2 rue paix"]))

        geocoder = EtalabSyncCsvGeocoder()
        results = [
            r async for r in geocoder.geocode_with_columns([{"address": "2 rue paix"}])
        ]

    assert len(results) == 1
    assert results[0]["found_result"]
    waits = [c.args[0] for c in fake_sleep.call_args_list]
    assert waits == [1, 2]


@pytest.mark.asyncio
async def test_giving_up_yields_error_rows(fake_sleep):
    """Chunk at min_subdivide_rows -> persistent failure -> one error row per input."""
    rows = [(f"addr_{i}", None) for i in range(10)]

    with aioresponses() as m:
        for _ in range(5):
            m.post(SEARCH_CSV_URL, status=500, body="boom")

        geocoder = EtalabSyncCsvGeocoder(min_subdivide_rows=10)
        results = [r async for r in geocoder.geocode(rows)]

    assert len(results) == 10
    for i, r in enumerate(results):
        assert r["found_result"] is False
        assert r["result_status"] == "error"
        assert r["postal_address"] == f"addr_{i}"


# -- Dict-path (geocode_with_columns) equivalents -----------------------------

OK_DICT_HEADER = (
    "address,siret,result_status,result_label,result_city,"
    "result_postcode,result_citycode,result_score,result_score_next,"
    "latitude,longitude"
)


def _ok_dict_body(rows: list[dict]) -> str:
    lines = [OK_DICT_HEADER]
    for row in rows:
        addr = row["address"]
        siret = row.get("siret", "")
        lines.append(
            f"{addr},{siret},ok,{addr} (matched),Paris,75002,75102,0.95,0.42,48.86,2.33"
        )
    return "\n".join(lines) + "\n"


@pytest.mark.asyncio
async def test_subdivide_on_persistent_failure_dict_path(fake_sleep):
    """200-dict chunk hits 5x 500 -> halved 100/100 -> each half succeeds. Order preserved."""
    rows = [{"address": f"addr_{i}", "siret": f"s{i}"} for i in range(200)]
    first_half = [{"address": f"addr_{i}", "siret": f"s{i}"} for i in range(100)]
    second_half = [{"address": f"addr_{i}", "siret": f"s{i}"} for i in range(100, 200)]

    with aioresponses() as m:
        for _ in range(5):
            m.post(SEARCH_CSV_URL, status=500, body="boom")
        m.post(SEARCH_CSV_URL, status=200, body=_ok_dict_body(first_half))
        m.post(SEARCH_CSV_URL, status=200, body=_ok_dict_body(second_half))

        geocoder = EtalabSyncCsvGeocoder(min_subdivide_rows=50)
        results = [r async for r in geocoder.geocode_with_columns(rows)]

    assert len(results) == 200
    assert results[0]["address"] == "addr_0"
    assert results[0]["siret"] == "s0"
    assert results[99]["address"] == "addr_99"
    assert results[100]["address"] == "addr_100"
    assert results[199]["address"] == "addr_199"
    for r in results:
        assert r["found_result"] is True


@pytest.mark.asyncio
async def test_giving_up_yields_error_rows_dict_path(fake_sleep):
    """Dict chunk at min_subdivide_rows -> persistent failure -> error rows with input echoed."""
    rows = [{"address": f"addr_{i}", "siret": f"s{i}"} for i in range(10)]

    with aioresponses() as m:
        for _ in range(5):
            m.post(SEARCH_CSV_URL, status=500, body="boom")

        geocoder = EtalabSyncCsvGeocoder(min_subdivide_rows=10)
        results = [r async for r in geocoder.geocode_with_columns(rows)]

    assert len(results) == 10
    for i, r in enumerate(results):
        assert r["found_result"] is False
        assert r["result_status"] == "error"
        assert r["address"] == f"addr_{i}"
        assert r["siret"] == f"s{i}"
