# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this package is

`etalab_apis` is a thin async wrapper around French government open APIs. Currently it exposes one client, `EtalabGpsApi`, which calls the BAN geocoding service at `api-adresse.data.gouv.fr`:

- forward geocoding: `search/?q=<address>&citycode=<insee>&limit=<n>` → returns first feature's coordinates + properties
- reverse geocoding: `reverse/?lon=<lng>&lat=<lat>`

The package is published-style (has `setup.py`, version is bumped per release, see `setup.py` and recent commits like `v:0.0.0.8`) and intended to be installed as a dependency in other projects. Keep the public surface (`EtalabGpsApi` class and its async methods) stable across patch versions; if a method's return shape changes, bump the version.

## Environment

- **Python**: 3.11 (per the global env note — `setup.py` says `>=3.10` but use 3.11 to stay consistent with other projects on this machine).
- **Venv**: `./venv` (already exists, gitignored).
- **No DB, no secrets, no .env** — this package only makes outbound HTTPS calls to the public Etalab API.

## Commands

```bash
# activate venv
source venv/bin/activate

# install / refresh deps
pip install -r requirements.txt
pip install -r requirements.txt --upgrade

# run all tests (async tests use pytest-asyncio)
pytest

# run one test
pytest tests/test_api_gps.py::test_get_gps_coordinates -v

# run the module's __main__ demo against the live API
python -m etalab_apis.api_gps
```

Tests **hit the live Etalab API** — they are integration tests, not mocked. They will fail without internet, and may flake if the upstream API is slow or returns 504.

## Architecture notes

Single source file: `etalab_apis/api_gps.py`. Everything below lives there.

- **Async-first.** Every public method on `EtalabGpsApi` is a coroutine. Callers drive them with `asyncio.run(...)` or schedule them inside an existing loop.
- **One `aiohttp.ClientSession` per call.** `get_gps_coordinates` and `get_address_from_gps` each open + close their own session. This is wasteful for large batches but kept simple deliberately; do not refactor to a shared session without checking call sites in dependent projects first.
- **Batching pattern in `batch_gps_coordinates`.** Input list is split with `np.array_split` into chunks of `max_calls = 5`, each chunk is fanned out with `asyncio.gather`, then a `0.1s` sleep between chunks acts as crude rate-limiting against the public API. If you increase `max_calls`, also revisit the sleep — Etalab's published limit is ~50 req/s/IP but 5 concurrent has been the safe working point.
- **Two input modes for batches.** `postal_addresses=[str, ...]` (full address strings) **or** `addresses_insees=[(address, insee_code), ...]`. They are mutually exclusive in practice — the code branches on whichever is truthy, `postal_addresses` wins if both are passed.
- **Return contract: always a dict.** `get_gps_coordinates` is guaranteed to return a dict, never `None` (commit `15ce5c8`). On miss/error the dict is `{'found_result': False, 'postal_address': <queried address>}`. On hit it adds `gps` (lng, lat tuple), `lat`, `lng`, `postcode`, `insee_city_code`, `city`, `postal_address` (the API's canonical label, which **overwrites** the queried address), and `found_result: True`. Downstream callers rely on `found_result` to branch.
- **Address length guard.** Addresses shorter than 4 chars short-circuit; addresses longer than 200 chars are truncated to 200 before the request (the Etalab API rejects out-of-range lengths with HTTP 400 — see commits `b6e7873`/`8322f6d`).
- **Error handling is print-based**, not exception-based or logged. 504s are silently swallowed (treated as "in progress / try again"). Other non-200s print status + body. JSON parse errors print and fall through. If you add proper logging, do not change the "always returns a dict" contract.

## Known rough edges (don't "clean up" without asking)

- `api_gps.py:53-55` — the `len(postal_address) < 4` branch writes into `result` while `result` is still `None`. It will raise `TypeError` for very short addresses. This is a real bug, not a stylistic issue; flag it before touching adjacent code.
- `get_address_from_gps` reads `gps_long_lat['long']` — note the key is `long`, not `lng` or `longitude`. Inconsistent with the rest of the module (which uses `lng`). Don't silently rename; it's part of the public surface.
- `_read_json_response` is used by both forward and reverse endpoints, which is why reverse results also carry `lat`/`lng`/`city`/etc. with the same shape.

## Release workflow

Version lives in `setup.py` (`version="0.0.0.X"`). On release: bump the version, commit, then tag:

```bash
git tag v0.0.0.X
git push origin v0.0.0.X
```

Recent commit messages follow `<change> v:0.0.0.X` — keep that style so the tag/commit/version stay aligned.
