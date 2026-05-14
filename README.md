# etalab_apis

Async wrapper around the French Géoplateforme geocoding API
(`https://data.geopf.fr/geocodage`). Replaces the deprecated
`api-adresse.data.gouv.fr` endpoint, which the BAN announced for
decommissioning end of January 2026.

Two clients, picked by workload:

| Client | Endpoint | Best for |
|---|---|---|
| `etalab_apis.api_gps.EtalabGpsApi` | `GET /search`, `GET /reverse` | Unitary lookups, small to medium batches, live single-address calls |
| `etalab_apis.sync_server.csv_geocoder.EtalabSyncCsvGeocoder` | `POST /search/csv`, `POST /reverse/csv` | Bulk loads — up to 200 000 rows per HTTP call |

The async-projects path (`POST /async/projects` + polling) is **not**
wrapped; it sends email-on-completion notifications and is a poor fit
for a programmatic library.

## Install

```bash
pip install -e .          # runtime only
pip install -e .[dev]     # with pytest, aioresponses
```

Requires Python ≥ 3.10. Dependencies: `aiohttp`, `tqdm`.

## Unitary client — `EtalabGpsApi`

```python
import asyncio
from etalab_apis.api_gps import EtalabGpsApi

async def main():
    api = EtalabGpsApi()

    # forward — full address
    r = await api.get_gps_coordinates("2 rue de la paix 75002 Paris")
    print(r["lat"], r["lng"], r["city"])

    # forward — partial address with INSEE and/or postcode filter
    r = await api.get_gps_coordinates(
        "2 rue de la paix", insee_city_code="75102", postcode="75002",
    )

    # reverse
    r = await api.get_address_from_gps((2.35222, 48.85661))

    # batch — accepts (addr, insee) or (addr, insee, postcode) tuples
    rs = await api.batch_gps_coordinates(addresses_insees=[
        ("2 rue de la paix", "75102", "75002"),
        ("29 rue de la paix", None, "75002"),
    ])

asyncio.run(main())
```

Concurrency on `batch_gps_coordinates` is capped at 20 by default — well
under the 50 req/IP/sec public limit. 429 and 5xx responses are retried
automatically (5 attempts, exponential backoff capped at 60 s, honoring
`retry-after`).

### Dict-in/dict-out with passthrough columns

`get_gps_coordinates_with_extras` echoes every input column verbatim on
the result, useful when you need to carry a join key (e.g. `siret`) into
downstream code:

```python
row = {"siret": "12345678900001", "address": "2 rue de la paix 75002 Paris"}
result = await api.get_gps_coordinates_with_extras(row)
# result["siret"] == "12345678900001"
# result["lat"], result["lng"], result["result_city"], result["result_postcode"], ...
```

Optional `citycode_column` / `postcode_column` name the input keys to
forward as filters.

## Bulk client — `EtalabSyncCsvGeocoder`

One HTTP request handles up to 200 000 rows. Input is a lazy iterable;
output is an async iterator of result dicts. Chunks the input as needed
and recursively subdivides on persistent 5xx failure (yields
`result_status="error"` rows if the floor `min_subdivide_rows` is hit).

```python
import asyncio
from etalab_apis.sync_server.csv_geocoder import EtalabSyncCsvGeocoder

async def main():
    geocoder = EtalabSyncCsvGeocoder()

    # tuple input: (addr, insee_or_None) or (addr, insee_or_None, postcode_or_None)
    rows = [
        ("2 rue de la paix 75002 Paris", None),
        ("29 rue de la paix", None, "75002"),
    ]
    async for r in geocoder.geocode(iter(rows)):
        if r["found_result"]:
            print(r["lat"], r["lng"], r["result_score"])
        else:
            print(r["result_status"], r["postal_address"])

    # reverse
    async for r in geocoder.reverse_geocode([(2.35222, 48.85661)]):
        ...

asyncio.run(main())
```

### Dict-in/dict-out with passthrough columns

`geocode_with_columns` lets you ship arbitrary extra columns through the
endpoint — the server echoes them on the response CSV, the wrapper
surfaces them on every result dict:

```python
rows = [
    {"siret": "12345678900001", "address": "2 rue de la paix 75002 Paris"},
    {"siret": "98765432100022", "address": "29 rue de la paix 75002 Paris"},
]
async for r in geocoder.geocode_with_columns(iter(rows)):
    # r["siret"], r["address"]                   — input echoed
    # r["lat"], r["lng"], r["result_score"]      — parsed convenience
    # r["result_city"], r["result_postcode"], r["result_label"]
    # r["result_status"], r["found_result"]
    ...
```

Filter columns can be named when they differ from the API's defaults:

```python
rows = [{"siret": "...", "addr": "...", "pc": "75002"}]
async for r in geocoder.geocode_with_columns(
    iter(rows),
    match_columns=("addr",),     # which column(s) to use as the query
    postcode_column="pc",        # which column to forward as the postcode filter
    citycode_column=None,        # likewise for INSEE
):
    ...
```

## Tests

```bash
pytest                                              # all (live + mocked)
pytest tests/test_api_gps_retry.py -v               # mocked retry paths only (offline)
pytest tests/test_sync_csv_geocoder_retry.py -v     # mocked bulk retry paths only
```

Live tests hit `data.geopf.fr/geocodage` — they need internet and may
flake on upstream slowness. Mocked tests cover paths the live server
won't reliably trigger (429 with `retry-after`, 5xx backoff,
persistent-failure subdivision, retry exhaustion).

## API reference notes

- Rate limit: 50 req/IP/sec public.
- Bulk cap: 200 000 rows or 50 MB per request.
- Result dict fields documented inline on each method's docstring.
- Background and endpoint details: see `api_doc.md` in this repo.
