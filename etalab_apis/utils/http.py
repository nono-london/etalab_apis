import contextlib

import aiohttp

GEOPF_BASE_URL = "https://data.geopf.fr/geocodage"
MAX_BACKOFF_SECONDS = 60


def retry_after_seconds(response: aiohttp.ClientResponse, default: float) -> float:
    raw = response.headers.get("retry-after")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


async def safe_text(response: aiohttp.ClientResponse) -> str:
    try:
        return await response.text()
    except Exception:
        return "<unreadable body>"


@contextlib.asynccontextmanager
async def session_for(
    provided: aiohttp.ClientSession | None,
    fallback: aiohttp.ClientSession | None,
):
    if provided is not None:
        yield provided
    elif fallback is not None:
        yield fallback
    else:
        async with aiohttp.ClientSession() as s:
            yield s


def normalize_forward_tuple(row: tuple) -> tuple[str, str | None, str | None]:
    if len(row) == 2:
        return row[0], row[1], None
    if len(row) == 3:
        return row[0], row[1], row[2]
    raise ValueError(f"row must be (addr, insee) or (addr, insee, postcode); got {row!r}")
