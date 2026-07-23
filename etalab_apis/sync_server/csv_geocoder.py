import asyncio
import csv
import io
import itertools
import logging
from typing import Any, AsyncIterator, Callable, Iterable, Iterator, Mapping, Sequence

import aiohttp

from etalab_apis.utils.http import (
    GEOPF_BASE_URL,
    MAX_BACKOFF_SECONDS,
    normalize_forward_tuple,
    retry_after_seconds,
    safe_text,
    session_for,
)

logger = logging.getLogger(__name__)

MAX_ROWS_PER_BATCH = 200_000
DEFAULT_MAX_RETRIES = 5
DEFAULT_MIN_SUBDIVIDE_ROWS = 100
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(sock_read=300)

_MODE_FORWARD = "forward"
_MODE_REVERSE = "reverse"


class _PersistentBatchFailure(Exception):
    pass


class EtalabSyncCsvGeocoder:
    """Bulk geocoder using POST /search/csv and /reverse/csv (synchronous batch mode).

    One HTTP request handles up to MAX_ROWS_PER_BATCH addresses; the server responds
    with the input CSV plus appended result_* columns. See api_doc.md S4.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession | None = None,
        base_url: str = GEOPF_BASE_URL,
        max_retries: int = DEFAULT_MAX_RETRIES,
        min_subdivide_rows: int = DEFAULT_MIN_SUBDIVIDE_ROWS,
        timeout: aiohttp.ClientTimeout = DEFAULT_TIMEOUT,
    ):
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._min_subdivide_rows = min_subdivide_rows
        self._timeout = timeout

    async def geocode(
        self,
        rows: Iterable[tuple],
        chunk_rows: int = MAX_ROWS_PER_BATCH,
    ) -> AsyncIterator[dict]:
        async with session_for(None, self._session, self._timeout) as s:
            iterator = (normalize_forward_tuple(r) for r in rows)
            while True:
                chunk = list(itertools.islice(iterator, chunk_rows))
                if not chunk:
                    return
                async for result in self._geocode_chunk(s, chunk, _MODE_FORWARD):
                    yield result

    async def geocode_with_columns(
        self,
        rows: Iterable[Mapping[str, Any]],
        match_columns: Sequence[str] = ("address",),
        citycode_column: str | None = None,
        postcode_column: str | None = None,
        chunk_rows: int = MAX_ROWS_PER_BATCH,
    ) -> AsyncIterator[dict]:
        """Bulk forward-geocode rows with arbitrary passthrough columns.

        Each input row is a dict (or any Mapping). The server echoes every input
        column on the corresponding output row, so any column not named in
        match_columns/citycode_column/postcode_column is carried through
        untouched -- useful for join keys like `siret`.

        Yields dicts: every column from the response CSV (input passthrough +
        result_* fields from the server) plus parsed convenience fields:
        lat, lng, gps, found_result, result_score, result_score_next,
        result_status (normalized).
        """
        if not match_columns:
            raise ValueError("match_columns must name at least one CSV column")
        async with session_for(None, self._session, self._timeout) as s:
            iterator = (dict(r) for r in rows)
            while True:
                chunk = list(itertools.islice(iterator, chunk_rows))
                if not chunk:
                    return
                async for result in self._geocode_dict_chunk(
                    s, chunk, match_columns, citycode_column, postcode_column,
                ):
                    yield result

    async def reverse_geocode(
        self,
        rows: Iterable[tuple[float, float]],
        chunk_rows: int = MAX_ROWS_PER_BATCH,
    ) -> AsyncIterator[dict]:
        async with session_for(None, self._session, self._timeout) as s:
            iterator = iter(rows)
            while True:
                chunk = list(itertools.islice(iterator, chunk_rows))
                if not chunk:
                    return
                async for result in self._geocode_chunk(s, chunk, _MODE_REVERSE):
                    yield result

    async def reverse_geocode_with_columns(
        self,
        rows: Iterable[Mapping[str, Any]],
        chunk_rows: int = MAX_ROWS_PER_BATCH,
    ) -> AsyncIterator[dict]:
        """Bulk reverse-geocode rows with arbitrary passthrough columns.

        Each input row is a dict (or any Mapping) that MUST contain the
        coordinate columns "lat" and "lon" (WGS84 floats); every other column
        is echoed back untouched by the server -- useful for join keys.

        Yields dicts: every column from the response CSV (input passthrough +
        the COMPLETE result_* payload: label, housenumber, name, street,
        postcode, city, citycode, type, id, banId, distance, oldcitycode,
        oldcity, district, status, ...) plus parsed convenience fields:
        lat, lng, gps (matched point when found, else the input point),
        found_result, result_score, result_distance, result_status
        (normalized).
        """
        async with session_for(None, self._session, self._timeout) as s:
            iterator = (dict(r) for r in rows)
            while True:
                chunk = list(itertools.islice(iterator, chunk_rows))
                if not chunk:
                    return
                for row in chunk:
                    if "lat" not in row or "lon" not in row:
                        raise ValueError(
                            f"reverse rows must contain 'lat' and 'lon' columns; got {list(row)!r}"
                        )
                async for result in self._reverse_dict_chunk(s, chunk):
                    yield result

    # -- chunking with subdivide-on-failure ------------------------------------

    async def _geocode_chunk(
        self,
        session: aiohttp.ClientSession,
        chunk: list,
        mode: str,
    ) -> AsyncIterator[dict]:
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

    async def _geocode_dict_chunk(
        self,
        session: aiohttp.ClientSession,
        chunk: list[dict],
        match_columns: Sequence[str],
        citycode_column: str | None,
        postcode_column: str | None,
    ) -> AsyncIterator[dict]:
        try:
            csv_text = await self._post_dict_chunk(
                session, chunk, match_columns, citycode_column, postcode_column,
            )
        except _PersistentBatchFailure as exc:
            if len(chunk) <= self._min_subdivide_rows:
                logger.error("giving up on chunk of %d rows: %s", len(chunk), exc)
                for row in chunk:
                    yield {**row, "found_result": False, "result_status": "error"}
                return
            mid = len(chunk) // 2
            logger.warning("subdividing chunk of %d after persistent failure: %s", len(chunk), exc)
            async for r in self._geocode_dict_chunk(
                session, chunk[:mid], match_columns, citycode_column, postcode_column,
            ):
                yield r
            async for r in self._geocode_dict_chunk(
                session, chunk[mid:], match_columns, citycode_column, postcode_column,
            ):
                yield r
            return

        for parsed in _parse_dict_response_csv(csv_text):
            yield parsed

    # -- HTTP POST with retry --------------------------------------------------

    async def _post_with_retry(
        self,
        session: aiohttp.ClientSession,
        url: str,
        build_form: Callable[[], aiohttp.FormData],
        label: str,
    ) -> str:
        last_failure = "no attempt made"

        for attempt in range(self._max_retries):
            is_last = attempt + 1 == self._max_retries

            async with session.post(url, data=build_form()) as resp:
                status = resp.status
                if status == 200:
                    try:
                        return await resp.text()
                    except aiohttp.ClientPayloadError as e:
                        last_failure = f"HTTP 200 truncated payload: {e}"
                        wait = min(2 ** attempt, MAX_BACKOFF_SECONDS)
                elif status == 429:
                    wait = retry_after_seconds(resp, default=5.0)
                    last_failure = f"HTTP 429 (rate-limited, retry-after={wait:.1f}s)"
                elif 500 <= status < 600:
                    body = await safe_text(resp)
                    last_failure = f"HTTP {status}: {body[:500]}"
                    wait = min(2 ** attempt, MAX_BACKOFF_SECONDS)
                else:
                    body = await safe_text(resp)
                    raise _PersistentBatchFailure(f"HTTP {status}: {body[:500]}")
            if is_last:
                break
            logger.warning(
                "retrying %s after %s (attempt %d/%d), backing off %ds",
                label, last_failure, attempt + 1, self._max_retries, wait,
            )
            await asyncio.sleep(wait)

        raise _PersistentBatchFailure(
            f"exhausted {self._max_retries} retries on {label}: {last_failure}"
        )

    async def _post_chunk(
        self,
        session: aiohttp.ClientSession,
        chunk: list,
        mode: str,
    ) -> str:
        csv_payload, has_insee, has_postcode = _build_input_csv(chunk, mode)
        endpoint = "/search/csv" if mode == _MODE_FORWARD else "/reverse/csv"
        url = f"{self._base_url}{endpoint}"

        def build_form() -> aiohttp.FormData:
            form = aiohttp.FormData()
            form.add_field(
                "data", csv_payload,
                filename="input.csv", content_type="text/csv; charset=utf-8",
            )
            if mode == _MODE_FORWARD:
                form.add_field("columns", "address")
                if has_insee:
                    form.add_field("citycode", "citycode")
                if has_postcode:
                    form.add_field("postcode", "postcode")
            form.add_field("indexes", "address")
            return form

        return await self._post_with_retry(session, url, build_form, endpoint)

    async def _reverse_dict_chunk(
        self,
        session: aiohttp.ClientSession,
        chunk: list[dict],
    ) -> AsyncIterator[dict]:
        try:
            csv_text = await self._post_reverse_dict_chunk(session, chunk)
        except _PersistentBatchFailure as exc:
            if len(chunk) <= self._min_subdivide_rows:
                logger.error("giving up on reverse chunk of %d rows: %s", len(chunk), exc)
                for row in chunk:
                    yield {**row, "found_result": False, "result_status": "error"}
                return
            mid = len(chunk) // 2
            logger.warning("subdividing reverse chunk of %d after persistent failure: %s",
                           len(chunk), exc)
            async for r in self._reverse_dict_chunk(session, chunk[:mid]):
                yield r
            async for r in self._reverse_dict_chunk(session, chunk[mid:]):
                yield r
            return

        for parsed in _parse_reverse_dict_response_csv(csv_text):
            yield parsed

    async def _post_reverse_dict_chunk(
        self,
        session: aiohttp.ClientSession,
        chunk: list[dict],
    ) -> str:
        csv_payload = _build_dict_csv(chunk)
        url = f"{self._base_url}/reverse/csv"

        def build_form() -> aiohttp.FormData:
            form = aiohttp.FormData()
            form.add_field(
                "data", csv_payload,
                filename="input.csv", content_type="text/csv; charset=utf-8",
            )
            return form

        return await self._post_with_retry(session, url, build_form, "/reverse/csv")

    async def _post_dict_chunk(
        self,
        session: aiohttp.ClientSession,
        chunk: list[dict],
        match_columns: Sequence[str],
        citycode_column: str | None,
        postcode_column: str | None,
    ) -> str:
        csv_payload = _build_dict_csv(chunk)
        url = f"{self._base_url}/search/csv"

        def build_form() -> aiohttp.FormData:
            form = aiohttp.FormData()
            form.add_field(
                "data", csv_payload,
                filename="input.csv", content_type="text/csv; charset=utf-8",
            )
            for col in match_columns:
                form.add_field("columns", col)
            if citycode_column:
                form.add_field("citycode", citycode_column)
            if postcode_column:
                form.add_field("postcode", postcode_column)
            form.add_field("indexes", "address")
            return form

        return await self._post_with_retry(session, url, build_form, "/search/csv")


# -- CSV building / parsing (module-level helpers) -----------------------------

def _build_input_csv(chunk: list, mode: str) -> tuple[bytes, bool, bool]:
    buf = io.StringIO()
    writer = csv.writer(buf)
    if mode == _MODE_FORWARD:
        has_insee = any(insee for _, insee, _ in chunk)
        has_postcode = any(pc for _, _, pc in chunk)
        header = ["address"]
        if has_insee:
            header.append("citycode")
        if has_postcode:
            header.append("postcode")
        writer.writerow(header)
        for addr, insee, postcode in chunk:
            row = [addr]
            if has_insee:
                row.append(insee or "")
            if has_postcode:
                row.append(postcode or "")
            writer.writerow(row)
        return buf.getvalue().encode("utf-8"), has_insee, has_postcode
    writer.writerow(["lon", "lat"])
    for lng, lat in chunk:
        writer.writerow([lng, lat])
    return buf.getvalue().encode("utf-8"), False, False


def _parse_response_csv(csv_text: str, mode: str):
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        yield _row_to_result(row, mode)


def _row_to_result(row: dict[str, str], mode: str) -> dict:
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
                "result_score": _to_float(row.get("result_score")),
                "result_score_next": _to_float(row.get("result_score_next")),
                "found_result": True,
                "result_status": status,
            }
        return {
            "gps": None,
            "lng": None,
            "lat": None,
            "postcode": None,
            "insee_city_code": None,
            "city": None,
            "postal_address": row.get("address"),
            "result_score": None,
            "result_score_next": None,
            "found_result": False,
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
            "result_score": _to_float(row.get("result_score")),
            "result_score_next": _to_float(row.get("result_score_next")),
            "found_result": True,
            "result_status": status,
        }
    lng = _to_float(row.get("lon"))
    lat = _to_float(row.get("lat"))
    return {
        "gps": None,
        "lng": lng,
        "lat": lat,
        "postcode": None,
        "insee_city_code": None,
        "city": None,
        "postal_address": None,
        "result_score": None,
        "result_score_next": None,
        "found_result": False,
        "result_status": status or "not-found",
    }


def _error_row(row, mode: str) -> dict:
    if mode == _MODE_FORWARD:
        return {
            "gps": None, "lng": None, "lat": None,
            "postcode": None, "insee_city_code": None, "city": None,
            "postal_address": row[0],
            "result_score": None, "result_score_next": None,
            "found_result": False, "result_status": "error",
        }
    lng, lat = row
    return {
        "gps": None, "lng": lng, "lat": lat,
        "postcode": None, "insee_city_code": None, "city": None,
        "postal_address": None,
        "result_score": None, "result_score_next": None,
        "found_result": False, "result_status": "error",
    }


def _to_float(s: str | None) -> float | None:
    if s is None or s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _build_dict_csv(chunk: list[dict]) -> bytes:
    fieldnames: list[str] = []
    seen = set()
    for row in chunk:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore", restval="")
    writer.writeheader()
    for row in chunk:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def _parse_dict_response_csv(csv_text: str) -> Iterator[dict]:
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        yield _dict_row_to_result(row)


def _parse_reverse_dict_response_csv(csv_text: str) -> Iterator[dict]:
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        yield _reverse_dict_row_to_result(row)


def _reverse_dict_row_to_result(row: dict[str, str]) -> dict:
    out = dict(row)
    status = (row.get("result_status") or "").strip()
    out["result_status"] = status or "not-found"
    found = status == "ok"
    out["found_result"] = found
    # matched point when found, else echo the input point
    lng = _to_float(row.get("result_longitude")) if found else _to_float(row.get("lon"))
    lat = _to_float(row.get("result_latitude")) if found else _to_float(row.get("lat"))
    out["lat"] = lat
    out["lng"] = lng
    out["gps"] = (lng, lat) if lng is not None and lat is not None else None
    out["result_score"] = _to_float(row.get("result_score"))
    out["result_distance"] = _to_float(row.get("result_distance"))
    return out


def _dict_row_to_result(row: dict[str, str]) -> dict:
    out = dict(row)
    status = (row.get("result_status") or "").strip()
    out["result_status"] = status or "not-found"
    out["found_result"] = status == "ok"
    lng = _to_float(row.get("longitude"))
    lat = _to_float(row.get("latitude"))
    out["lat"] = lat
    out["lng"] = lng
    out["gps"] = (lng, lat) if lng is not None and lat is not None else None
    out["result_score"] = _to_float(row.get("result_score"))
    out["result_score_next"] = _to_float(row.get("result_score_next"))
    return out
