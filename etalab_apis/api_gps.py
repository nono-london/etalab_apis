import asyncio
import contextlib
import logging
from time import time
from typing import Dict, List, Optional, Tuple, Union

import aiohttp
from tqdm import tqdm

from etalab_apis.utils.http import MAX_BACKOFF_SECONDS, retry_after_seconds, safe_text

logger = logging.getLogger(__name__)

GEOPF_BASE_URL = "https://data.geopf.fr/geocodage"
DEFAULT_MAX_CONCURRENT = 20
DEFAULT_MAX_RETRIES = 5


class EtalabGpsApi:
    def __init__(
        self,
        session: Optional[aiohttp.ClientSession] = None,
        base_url: str = GEOPF_BASE_URL,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._max_concurrent = max_concurrent
        self._max_retries = max_retries

    @contextlib.asynccontextmanager
    async def _session_for(self, session: Optional[aiohttp.ClientSession]):
        if session is not None:
            yield session
        elif self._session is not None:
            yield self._session
        else:
            async with aiohttp.ClientSession() as s:
                yield s

    def _build_search_request(
        self,
        postal_address: str,
        insee_city_code: Optional[str] = None,
        limit: int = 1,
    ) -> Tuple[str, Dict[str, str]]:
        params: Dict[str, str] = {
            "q": postal_address,
            "index": "address",
            "limit": str(limit),
        }
        if insee_city_code:
            params["citycode"] = insee_city_code
        return f"{self._base_url}/search", params

    def _build_reverse_request(
        self, lng: float, lat: float, limit: int = 1
    ) -> Tuple[str, Dict[str, str]]:
        params: Dict[str, str] = {
            "lon": str(lng),
            "lat": str(lat),
            "index": "address",
            "limit": str(limit),
        }
        return f"{self._base_url}/reverse", params

    @staticmethod
    def _read_json_response(json_response: dict) -> Optional[Dict]:
        features = json_response.get("features")
        if not features:
            return None
        feature = features[0]
        coords = feature.get("geometry", {}).get("coordinates")
        if not coords:
            return None
        props = feature.get("properties", {})
        return {
            "gps": tuple(coords),
            "lng": coords[0],
            "lat": coords[1],
            "postcode": props.get("postcode"),
            "insee_city_code": props.get("citycode"),
            "city": props.get("city"),
            "postal_address": props.get("label"),
        }

    async def _get_json_with_retry(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: Dict[str, str],
        ctx: str,
    ) -> Optional[Dict]:
        """GET url with retry on 429/5xx. Returns parsed JSON dict, or None on terminal failure.

        Retries:
        - 429: honors retry-after header, retries until exhaustion.
        - 5xx / 504: exponential backoff capped at MAX_BACKOFF_SECONDS, retries until exhaustion.
        - 4xx-non-429: no retry, logs and returns None.
        - JSON parse failure on 200: logs and returns None.
        """
        for attempt in range(self._max_retries):
            is_last = attempt + 1 == self._max_retries
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    try:
                        return await response.json()
                    except Exception:
                        logger.exception("failed to parse JSON for %s", ctx)
                        return None
                if response.status == 429:
                    wait = retry_after_seconds(response, default=5.0)
                    if is_last:
                        logger.error("429 rate-limited for %s, retries exhausted", ctx)
                        return None
                    logger.warning("429 for %s, sleeping %.1fs", ctx, wait)
                    await asyncio.sleep(wait)
                    continue
                if response.status == 504 or 500 <= response.status < 600:
                    if is_last:
                        logger.error(
                            "HTTP %s for %s, retries exhausted", response.status, ctx
                        )
                        return None
                    wait = min(2 ** attempt, MAX_BACKOFF_SECONDS)
                    logger.warning(
                        "HTTP %s for %s (attempt %d/%d), backing off %ds",
                        response.status, ctx, attempt + 1, self._max_retries, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                body = await safe_text(response)
                logger.error("HTTP %s for %s: %s", response.status, ctx, body)
                return None
        return None

    async def get_gps_coordinates(
        self,
        postal_address: str,
        insee_city_code: Optional[str] = None,
        limit: int = 1,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> Dict:
        if len(postal_address) < 4:
            return {"found_result": False, "postal_address": postal_address}
        postal_address = postal_address[:200]
        url, params = self._build_search_request(postal_address, insee_city_code, limit)

        async with self._session_for(session) as s:
            json_response = await self._get_json_with_retry(s, url, params, postal_address)

        result = self._read_json_response(json_response) if json_response is not None else None
        if result is None:
            return {"found_result": False, "postal_address": postal_address}
        result["found_result"] = True
        return result

    async def batch_gps_coordinates(
        self,
        postal_addresses: Optional[List[str]] = None,
        addresses_insees: Optional[List[Tuple[str, Optional[str]]]] = None,
    ) -> List[Dict]:
        if postal_addresses:
            items: List[Tuple[str, Optional[str]]] = [(addr, None) for addr in postal_addresses]
        elif addresses_insees:
            items = list(addresses_insees)
        else:
            return []

        sem = asyncio.Semaphore(self._max_concurrent)
        pbar = tqdm(total=len(items))

        async def bounded(s: aiohttp.ClientSession, addr: str, insee: Optional[str]) -> Dict:
            async with sem:
                try:
                    return await self.get_gps_coordinates(addr, insee, session=s)
                finally:
                    pbar.update(1)

        async with self._session_for(None) as session:
            try:
                return await asyncio.gather(*(bounded(session, a, i) for a, i in items))
            finally:
                pbar.close()

    async def get_address_from_gps(
        self,
        gps_long_lat: Union[Dict, Tuple],
        limit: int = 1,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> Optional[Dict]:
        if isinstance(gps_long_lat, dict):
            lng = gps_long_lat["lng"]
            lat = gps_long_lat["lat"]
        else:
            lng, lat = gps_long_lat[0], gps_long_lat[1]
        if lng is None or lat is None:
            return None

        url, params = self._build_reverse_request(lng, lat, limit)
        ctx = f"reverse ({lng}, {lat})"
        async with self._session_for(session) as s:
            json_response = await self._get_json_with_retry(s, url, params, ctx)
        if json_response is None:
            return None
        return self._read_json_response(json_response)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    start = time()

    my_postal_addresses = [
        ("VILLARS LES DOMBES", "01443"),
        ("DIVONNE LES BAINS", "01143"),
        ("YZEURE", "03400"),
    ]

    dvf_api = EtalabGpsApi()
    try:
        gps_datas = asyncio.run(dvf_api.batch_gps_coordinates(addresses_insees=my_postal_addresses))
        print(gps_datas)
        postal_address = "1 FOND DE BOSSART 08460 NEUFMAISON"
        gps_datas = asyncio.run(dvf_api.get_gps_coordinates(postal_address=postal_address))
        print(gps_datas)
    except Exception as e:
        print(e)

    print(f"App ran in {round(time() - start, 3)} seconds")
