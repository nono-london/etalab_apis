import aiohttp

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
