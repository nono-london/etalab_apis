import logging
from typing import AsyncIterator, Dict, Iterable, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

GEOPF_BASE_URL = "https://data.geopf.fr/geocodage"


class EtalabAsyncProjectGeocoder:
    """Bulk geocoder using the /async/projects/* workflow.

    Stateful 5-step pipeline: create project -> set pipeline params -> upload input
    file -> start -> poll until completed -> download output. See api_doc.md §5.

    Anonymous: 50 MB input cap, concurrency=1. With a Geoplateforme bearer token
    (and optional X-Community header) quotas can be lifted up to 1 GB.
    """

    def __init__(
        self,
        session: Optional[aiohttp.ClientSession] = None,
        base_url: str = GEOPF_BASE_URL,
        auth_token: Optional[str] = None,
        community: Optional[str] = None,
        poll_interval: int = 30,
    ):
        self._session = session
        self._owns_session = session is None
        self._base_url = base_url.rstrip("/")
        self._auth_token = auth_token
        self._community = community
        self._poll_interval = poll_interval

    async def geocode(
        self,
        rows: Iterable[Tuple[str, Optional[str]]],
    ) -> AsyncIterator[Dict]:
        """Forward-geocode an iterable of (address, insee_code | None) tuples."""
        raise NotImplementedError

    async def reverse_geocode(
        self,
        rows: Iterable[Tuple[float, float]],
    ) -> AsyncIterator[Dict]:
        """Reverse-geocode an iterable of (lon, lat) tuples."""
        raise NotImplementedError
