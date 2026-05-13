import logging
from typing import AsyncIterator, Dict, Iterable, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

GEOPF_BASE_URL = "https://data.geopf.fr/geocodage"
MAX_ROWS_PER_BATCH = 200_000


class EtalabSyncCsvGeocoder:
    """Bulk geocoder using POST /search/csv and /reverse/csv (synchronous batch mode).

    One HTTP request handles up to MAX_ROWS_PER_BATCH addresses; the server responds
    with the input CSV plus appended result_* columns. See api_doc.md §4.
    """

    def __init__(
        self,
        session: Optional[aiohttp.ClientSession] = None,
        base_url: str = GEOPF_BASE_URL,
        max_concurrent_batches: int = 1,
    ):
        self._session = session
        self._owns_session = session is None
        self._base_url = base_url.rstrip("/")
        self._max_concurrent_batches = max_concurrent_batches

    async def geocode(
        self,
        rows: Iterable[Tuple[str, Optional[str]]],
        chunk_rows: int = MAX_ROWS_PER_BATCH,
    ) -> AsyncIterator[Dict]:
        """Forward-geocode an iterable of (address, insee_code | None) tuples.

        Yields dicts shaped like api_gps.EtalabGpsApi.get_gps_coordinates output,
        plus per-row result_status from the batch response.
        """
        raise NotImplementedError

    async def reverse_geocode(
        self,
        rows: Iterable[Tuple[float, float]],
        chunk_rows: int = MAX_ROWS_PER_BATCH,
    ) -> AsyncIterator[Dict]:
        """Reverse-geocode an iterable of (lon, lat) tuples."""
        raise NotImplementedError
