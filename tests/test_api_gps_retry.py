"""Mocked-HTTP tests for the retry/backoff paths in EtalabGpsApi.

The live server won't return 429 or 5xx on demand, so we drive these paths
with aioresponses and a patched asyncio.sleep (same pattern as
test_sync_csv_geocoder_retry.py).
"""

import json
import re
from unittest.mock import AsyncMock

import pytest
from aioresponses import aioresponses

from etalab_apis.api_gps import EtalabGpsApi

SEARCH_URL_RE = re.compile(r"^https://data\.geopf\.fr/geocodage/search(\?.*)?$")

OK_BODY = json.dumps(
    {
        "features": [
            {
                "geometry": {"type": "Point", "coordinates": [2.33, 48.86]},
                "properties": {
                    "label": "2 Rue de la Paix 75002 Paris",
                    "postcode": "75002",
                    "citycode": "75102",
                    "city": "Paris",
                },
            }
        ]
    }
)


@pytest.fixture
def fake_sleep(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr("etalab_apis.api_gps.asyncio.sleep", mock)
    return mock


@pytest.mark.asyncio
async def test_429_retry_honors_retry_after(fake_sleep):
    """429 with retry-after: 3 -> sleeps 3s then succeeds on second attempt."""
    with aioresponses() as m:
        m.get(SEARCH_URL_RE, status=429, headers={"retry-after": "3"})
        m.get(SEARCH_URL_RE, status=200, body=OK_BODY)

        api = EtalabGpsApi()
        result = await api.get_gps_coordinates("2 rue de la paix 75002 Paris")

    assert result["found_result"] is True
    assert result["city"] == "Paris"
    fake_sleep.assert_called_once_with(3.0)


@pytest.mark.asyncio
async def test_5xx_exponential_backoff_then_success(fake_sleep):
    """3 consecutive 5xx -> backoff [1, 2, 4] -> 4th attempt succeeds."""
    with aioresponses() as m:
        m.get(SEARCH_URL_RE, status=500)
        m.get(SEARCH_URL_RE, status=502)
        m.get(SEARCH_URL_RE, status=503)
        m.get(SEARCH_URL_RE, status=200, body=OK_BODY)

        api = EtalabGpsApi()
        result = await api.get_gps_coordinates("2 rue de la paix 75002 Paris")

    assert result["found_result"] is True
    waits = [c.args[0] for c in fake_sleep.call_args_list]
    assert waits == [1, 2, 4]


@pytest.mark.asyncio
async def test_retries_exhausted_returns_not_found_dict(fake_sleep):
    """All 5 attempts return 500 -> exhausts retries -> not-found dict (contract preserved)."""
    with aioresponses() as m:
        for _ in range(5):
            m.get(SEARCH_URL_RE, status=500)

        api = EtalabGpsApi()
        result = await api.get_gps_coordinates("2 rue de la paix 75002 Paris")

    assert result == {
        "found_result": False,
        "postal_address": "2 rue de la paix 75002 Paris",
        "result_status": "not-found",
    }


@pytest.mark.asyncio
async def test_4xx_non_429_does_not_retry(fake_sleep):
    """400 Bad Request -> no retry, returns not-found dict immediately."""
    with aioresponses() as m:
        m.get(SEARCH_URL_RE, status=400, body="bad request")

        api = EtalabGpsApi()
        result = await api.get_gps_coordinates("2 rue de la paix 75002 Paris")

    assert result["found_result"] is False
    fake_sleep.assert_not_called()
