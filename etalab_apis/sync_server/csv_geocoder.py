import asyncio
import contextlib
import csv
import io
import itertools
import logging
from typing import AsyncIterator, Dict, Iterable, List, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

GEOPF_BASE_URL = "https://data.geopf.fr/geocodage"
MAX_ROWS_PER_BATCH = 200_000
DEFAULT_MAX_RETRIES = 5
DEFAULT_MIN_SUBDIVIDE_ROWS = 100
MAX_BACKOFF_SECONDS = 60

_MODE_FORWARD = "forward"
_MODE_REVERSE = "reverse"


class _PersistentBatchFailure(Exception):
    pass


class EtalabSyncCsvGeocoder:
    """Bulk geocoder using POST /search/csv and /reverse/csv (synchronous batch mode).

    One HTTP request handles up to MAX_ROWS_PER_BATCH addresses; the server responds
    with the input CSV plus appended result_* columns. See api_doc.md §4.
    """

    def __init__(
        self,
        session: Optional[aiohttp.ClientSession] = None,
        base_url: str = GEOPF_BASE_URL,
        max_retries: int = DEFAULT_MAX_RETRIES,
        min_subdivide_rows: int = DEFAULT_MIN_SUBDIVIDE_ROWS,
    ):
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._min_subdivide_rows = min_subdivide_rows

    @contextlib.asynccontextmanager
    async def _session_for(self, session: Optional[aiohttp.ClientSession]):
        if session is not None:
            yield session
        elif self._session is not None:
            yield self._session
        else:
            async with aiohttp.ClientSession() as s:
                yield s

    async def geocode(
        self,
        rows: Iterable[Tuple[str, Optional[str]]],
        chunk_rows: int = MAX_ROWS_PER_BATCH,
    ) -> AsyncIterator[Dict]:
        async with self._session_for(None) as session:
            iterator = iter(rows)
            while True:
                chunk = list(itertools.islice(iterator, chunk_rows))
                if not chunk:
                    return
                async for result in self._geocode_chunk(session, chunk, _MODE_FORWARD):
                    yield result

    async def reverse_geocode(
        self,
        rows: Iterable[Tuple[float, float]],
        chunk_rows: int = MAX_ROWS_PER_BATCH,
    ) -> AsyncIterator[Dict]:
        async with self._session_for(None) as session:
            iterator = iter(rows)
            while True:
                chunk = list(itertools.islice(iterator, chunk_rows))
                if not chunk:
                    return
                async for result in self._geocode_chunk(session, chunk, _MODE_REVERSE):
                    yield result

    async def _geocode_chunk(
        self,
        session: aiohttp.ClientSession,
        chunk: List,
        mode: str,
    ) -> AsyncIterator[Dict]:
        try:
            csv_text = await self._post_chunk(session, chunk, mode)
        except _PersistentBatchFailure as exc:
            if len(chunk) <= self._min_subdivide_rows:
                logger.error("giving up on chunk of %d rows: %s", len(chunk), exc)
                for row in chunk:
                    yield _error_row(row, mode)
                return
            mid = len(chunk) // 2
            logger.warning("subdividing chunk of %d after persistent failure: %s", len(chunk), exc)
            async for r in self._geocode_chunk(session, chunk[:mid], mode):
                yield r
            async for r in self._geocode_chunk(session, chunk[mid:], mode):
                yield r
            return

        for parsed in _parse_response_csv(csv_text, mode):
            yield parsed

    async def _post_chunk(
        self,
        session: aiohttp.ClientSession,
        chunk: List,
        mode: str,
    ) -> str:
        csv_payload, has_insee = _build_input_csv(chunk, mode)
        endpoint = "/search/csv" if mode == _MODE_FORWARD else "/reverse/csv"
        url = f"{self._base_url}{endpoint}"

        for attempt in range(self._max_retries):
            form = aiohttp.FormData()
            form.add_field(
                "data",
                csv_payload,
                filename="input.csv",
                content_type="text/csv; charset=utf-8",
            )
            if mode == _MODE_FORWARD:
                form.add_field("columns", "address")
                if has_insee:
                    form.add_field("citycode", "citycode")
            form.add_field("indexes", "address")

            async with session.post(url, data=form) as resp:
                if resp.status == 200:
                    return await resp.text()
                if resp.status == 429:
                    wait = _retry_after_seconds(resp, default=5.0)
                    logger.warning("429 too many requests, sleeping %.1fs", wait)
                    await asyncio.sleep(wait)
                    continue
                if 500 <= resp.status < 600:
                    wait = min(2 ** attempt, MAX_BACKOFF_SECONDS)
                    body = await _safe_text(resp)
                    logger.warning(
                        "HTTP %s on %s (attempt %d/%d), backing off %ds: %s",
                        resp.status, endpoint, attempt + 1, self._max_retries, wait, body[:200],
                    )
                    await asyncio.sleep(wait)
                    continue
                body = await _safe_text(resp)
                raise _PersistentBatchFailure(f"HTTP {resp.status}: {body[:500]}")

        raise _PersistentBatchFailure(f"exhausted {self._max_retries} retries on {endpoint}")


def _build_input_csv(chunk: List, mode: str) -> Tuple[bytes, bool]:
    buf = io.StringIO()
    writer = csv.writer(buf)
    if mode == _MODE_FORWARD:
        has_insee = any(insee for _, insee in chunk)
        if has_insee:
            writer.writerow(["address", "citycode"])
            for addr, insee in chunk:
                writer.writerow([addr, insee or ""])
        else:
            writer.writerow(["address"])
            for addr, _ in chunk:
                writer.writerow([addr])
        return buf.getvalue().encode("utf-8"), has_insee
    writer.writerow(["lon", "lat"])
    for lng, lat in chunk:
        writer.writerow([lng, lat])
    return buf.getvalue().encode("utf-8"), False


def _parse_response_csv(csv_text: str, mode: str):
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        yield _row_to_result(row, mode)


def _row_to_result(row: Dict[str, str], mode: str) -> Dict:
    status = (row.get("result_status") or "").strip()
    found = status == "ok"
    if mode == _MODE_FORWARD:
        if found:
            lng = _to_float(row.get("longitude"))
            lat = _to_float(row.get("latitude"))
            return {
                "gps": (lng, lat) if lng is not None and lat is not None else None,
                "lng": lng,
                "lat": lat,
                "postcode": row.get("result_postcode") or None,
                "insee_city_code": row.get("result_citycode") or None,
                "city": row.get("result_city") or None,
                "postal_address": row.get("result_label") or row.get("address"),
                "found_result": True,
                "result_status": status,
            }
        return {
            "found_result": False,
            "postal_address": row.get("address"),
            "result_status": status or "not-found",
        }
    if found:
        lng = _to_float(row.get("result_longitude"))
        lat = _to_float(row.get("result_latitude"))
        return {
            "gps": (lng, lat) if lng is not None and lat is not None else None,
            "lng": lng,
            "lat": lat,
            "postcode": row.get("result_postcode") or None,
            "insee_city_code": row.get("result_citycode") or None,
            "city": row.get("result_city") or None,
            "postal_address": row.get("result_label"),
            "found_result": True,
            "result_status": status,
        }
    return {
        "found_result": False,
        "lng": _to_float(row.get("lon")),
        "lat": _to_float(row.get("lat")),
        "result_status": status or "not-found",
    }


def _error_row(row, mode: str) -> Dict:
    if mode == _MODE_FORWARD:
        addr, _ = row
        return {"found_result": False, "postal_address": addr, "result_status": "error"}
    lng, lat = row
    return {"found_result": False, "lng": lng, "lat": lat, "result_status": "error"}


def _to_float(s: Optional[str]) -> Optional[float]:
    if s is None or s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _retry_after_seconds(response: aiohttp.ClientResponse, default: float) -> float:
    raw = response.headers.get("retry-after")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


async def _safe_text(response: aiohttp.ClientResponse) -> str:
    try:
        return await response.text()
    except Exception:
        return "<unreadable body>"
