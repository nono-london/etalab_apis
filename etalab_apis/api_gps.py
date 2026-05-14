import asyncio
import logging
from time import time
from typing import Any, Mapping

import aiohttp
from tqdm import tqdm

from etalab_apis.utils.http import (
    GEOPF_BASE_URL,
    MAX_BACKOFF_SECONDS,
    normalize_forward_tuple,
    retry_after_seconds,
    safe_text,
    session_for,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENT = 20
DEFAULT_MAX_RETRIES = 5


class EtalabGpsApi:
    def __init__(
        self,
        session: aiohttp.ClientSession | None = None,
        base_url: str = GEOPF_BASE_URL,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._max_concurrent = max_concurrent
        self._max_retries = max_retries

    def _build_search_request(
        self,
        postal_address: str,
        insee_city_code: str | None = None,
        postcode: str | None = None,
        limit: int = 1,
    ) -> tuple[str, dict[str, str]]:
        params: dict[str, str] = {
            "q": postal_address,
            "index": "address",
            "limit": str(limit),
        }
        if insee_city_code:
            params["citycode"] = insee_city_code
        if postcode:
            params["postcode"] = postcode
        return f"{self._base_url}/search", params

    def _build_reverse_request(
        self, lng: float, lat: float, limit: int = 1
    ) -> tuple[str, dict[str, str]]:
        params: dict[str, str] = {
            "lon": str(lng),
            "lat": str(lat),
            "index": "address",
            "limit": str(limit),
        }
        return f"{self._base_url}/reverse", params

    @staticmethod
    def _read_json_response(json_response: dict) -> dict | None:
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
            "result_score": props.get("score"),
        }

    async def _get_json_with_retry(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: dict[str, str],
        ctx: str,
    ) -> dict | None:
        for attempt in range(self._max_retries):
            is_last = attempt + 1 == self._max_retries
            async with session.get(url, params=params) as response:
                status = response.status
                if status == 200:
                    try:
                        return await response.json()
                    except Exception:
                        logger.exception("failed to parse JSON for %s", ctx)
                        return None
                if status == 429:
                    wait = retry_after_seconds(response, default=5.0)
                elif 500 <= status < 600:
                    wait = min(2 ** attempt, MAX_BACKOFF_SECONDS)
                else:
                    body = await safe_text(response)
                    logger.error("HTTP %s for %s: %s", status, ctx, body)
                    return None
            if is_last:
                logger.error("HTTP %s for %s, retries exhausted", status, ctx)
                return None
            if status == 429:
                logger.warning("429 for %s, sleeping %.1fs", ctx, wait)
            else:
                logger.warning(
                    "HTTP %s for %s (attempt %d/%d), backing off %ds",
                    status, ctx, attempt + 1, self._max_retries, wait,
                )
            await asyncio.sleep(wait)
        return None

    async def get_gps_coordinates(
        self,
        postal_address: str,
        insee_city_code: str | None = None,
        postcode: str | None = None,
        limit: int = 1,
        session: aiohttp.ClientSession | None = None,
    ) -> dict:
        if len(postal_address) < 4:
            return {"found_result": False, "postal_address": postal_address}
        postal_address = postal_address[:200]
        url, params = self._build_search_request(postal_address, insee_city_code, postcode, limit)

        async with session_for(session, self._session) as s:
            json_response = await self._get_json_with_retry(s, url, params, postal_address)

        result = self._read_json_response(json_response) if json_response is not None else None
        if result is None:
            return {"found_result": False, "postal_address": postal_address}
        result["found_result"] = True
        return result

    async def get_gps_coordinates_with_extras(
        self,
        row: Mapping[str, Any],
        address_column: str = "address",
        citycode_column: str | None = None,
        postcode_column: str | None = None,
        limit: int = 1,
        session: aiohttp.ClientSession | None = None,
    ) -> dict:
        """Geocode a single dict-shaped row; preserve all input keys, attach result fields.

        Returns the input row echoed verbatim plus parsed result fields under
        result_*-style names for symmetry with EtalabSyncCsvGeocoder.geocode_with_columns.
        """
        addr = row[address_column]
        insee = row.get(citycode_column) if citycode_column else None
        pc = row.get(postcode_column) if postcode_column else None
        canonical = await self.get_gps_coordinates(
            postal_address=addr,
            insee_city_code=insee,
            postcode=pc,
            limit=limit,
            session=session,
        )
        out = dict(row)
        found = canonical.get("found_result", False)
        out["found_result"] = found
        out["lat"] = canonical.get("lat")
        out["lng"] = canonical.get("lng")
        out["gps"] = canonical.get("gps")
        out["result_score"] = canonical.get("result_score")
        out["result_score_next"] = None
        out["result_label"] = canonical.get("postal_address")
        out["result_city"] = canonical.get("city")
        out["result_postcode"] = canonical.get("postcode")
        out["result_citycode"] = canonical.get("insee_city_code")
        out["result_status"] = "ok" if found else "not-found"
        return out

    async def batch_gps_coordinates(
        self,
        postal_addresses: list[str] | None = None,
        addresses_insees: list[tuple] | None = None,
    ) -> list[dict]:
        if postal_addresses and addresses_insees:
            raise ValueError("pass either postal_addresses or addresses_insees, not both")
        if postal_addresses:
            items: list[tuple[str, str | None, str | None]] = [
                (addr, None, None) for addr in postal_addresses
            ]
        elif addresses_insees:
            items = [normalize_forward_tuple(row) for row in addresses_insees]
        else:
            return []

        sem = asyncio.Semaphore(self._max_concurrent)
        pbar = tqdm(total=len(items))

        async def bounded(
            s: aiohttp.ClientSession,
            addr: str,
            insee: str | None,
            postcode: str | None,
        ) -> dict:
            async with sem:
                try:
                    return await self.get_gps_coordinates(addr, insee, postcode=postcode, session=s)
                finally:
                    pbar.update(1)

        async with session_for(None, self._session) as s:
            try:
                return list(await asyncio.gather(*(bounded(s, a, i, pc) for a, i, pc in items)))
            finally:
                pbar.close()

    async def get_address_from_gps(
        self,
        gps_long_lat: dict | tuple,
        limit: int = 1,
        session: aiohttp.ClientSession | None = None,
    ) -> dict:
        not_found = {"found_result": False, "lng": "", "lat": ""}
        if isinstance(gps_long_lat, dict):
            lng = gps_long_lat["lng"]
            lat = gps_long_lat["lat"]
        else:
            lng, lat = gps_long_lat[0], gps_long_lat[1]
        if lng is None or lat is None:
            return not_found

        url, params = self._build_reverse_request(lng, lat, limit)
        ctx = f"reverse ({lng}, {lat})"
        async with session_for(session, self._session) as s:
            json_response = await self._get_json_with_retry(s, url, params, ctx)
        if json_response is None:
            return not_found
        return self._read_json_response(json_response) or not_found


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    start = time()

    my_postal_addresses = [
        ("VILLARS LES DOMBES", "01443"),
        ("DIVONNE LES BAINS", "01143"),
        ("YZEURE", "03400"),
    ]

    dvf_api = EtalabGpsApi()
    gps_datas = asyncio.run(dvf_api.batch_gps_coordinates(addresses_insees=my_postal_addresses))
    print(gps_datas)
    postal_address = "1 FOND DE BOSSART 08460 NEUFMAISON"
    gps_datas = asyncio.run(dvf_api.get_gps_coordinates(postal_address=postal_address))
    print(gps_datas)

    print(f"App ran in {round(time() - start, 3)} seconds")
