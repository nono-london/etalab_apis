# Géoplateforme Géocodage API — research notes

Captured by Claude during the migration off `api-adresse.data.gouv.fr` on 2026-05-13. Sources: the official [doc page](https://cartes.gouv.fr/aide/fr/guides-utilisateur/utiliser-les-services-de-la-geoplateforme/geocodage), the [BAN deprecation notice](https://adresse.data.gouv.fr/outils/api-doc/adresse), and the [OpenAPI YAML](https://data.geopf.fr/geocodage/openapi.yaml).

---

## 0. URL migration — read this first

The old endpoint `https://api-adresse.data.gouv.fr/search/...` was **officially announced for decommissioning end of January 2026**. As of 2026-05-13 it still answers HTTP 200, but that is unsupported, time-limited reprieve. New code must use the Géoplateforme URLs below.

| Old (deprecated) | New |
|---|---|
| `https://api-adresse.data.gouv.fr/search/?q=…&citycode=…&limit=…` | `https://data.geopf.fr/geocodage/search?q=…&citycode=…&index=address&limit=…` |
| `https://api-adresse.data.gouv.fr/reverse/?lon=…&lat=…` | `https://data.geopf.fr/geocodage/reverse?lon=…&lat=…&index=address` |

**Response shapes are identical** for the GET endpoints (verified by curl). The only behavioural change is that the new service indexes addresses + POIs + parcels in one corpus, so we must pass `index=address` explicitly to keep current address-only behaviour.

---

## 1. Base URL & service identity

- **Base URL:** `https://data.geopf.fr/geocodage`
- **Title:** *API Géoplateforme - Géocodage*
- **Version:** 1.0.0
- **Contact:** geoplateforme@ign.fr
- **License:** Licence Ouverte 2.0
- **Capabilities probe (no params):** `GET /getCapabilities`

Data sources (per official docs):
- Adresses: BAN, refreshed **weekly**.
- POIs: BD TOPO®, refreshed **quarterly**.
- Cadastral parcels: Parcellaire Express (PCI), refreshed **quarterly**.

---

## 2. Rate limits, quotas, and authentication

### Public (anonymous) limits
- **50 requests / IP / second**, hard ceiling.
- Over-limit: server returns **HTTP 429 Too Many Requests** with a `retry-after` header (initially 5 s, decreases as the over-call stops). Block lasts until both: the over-call ceases **and** `retry-after` hits 0.
- File-size cap for `POST /search/csv` and `POST /reverse/csv`: **50 MB** or **200 000 rows** (whichever is smaller).
- Async-project file-size cap, anonymous: **50 MB**, server-side `concurrency=1`.

### Authenticated (Géoplateforme account) — async path only
- Pass `Authorization: Bearer <token>` on `POST /async/projects`.
- Optional `X-Community: <community-id>` header inherits that community's quotas (bigger file size, server-side parallelism).
- IGN can lift the per-IP rate limit for **public-sector use cases** on request — fill out the form at https://cartes.gouv.fr/aide/fr/nous-ecrire.
- Project-scoped calls (`/async/projects/{id}/*`) use a different scheme: `Authorization: <project-token>` (returned by the create call — must be persisted client-side, only emitted once).

---

## 3. Unitary geocoding — `GET /search` and `GET /reverse`

### `GET /search`
Returns a GeoJSON `FeatureCollection` ordered by relevance.

| Param | Type | Notes |
|---|---|---|
| `q` | string | Free-text query (e.g. `8 bd du Port`). Optional only when `index=parcel` with structured filters. |
| `index` | string | `address`, `poi`, `parcel`, or a comma-separated combination. **Pass `address` for address-only behaviour.** |
| `limit` | int | Default 10, max 50 (auto-clamped to 20 if `returntruegeometry=true`). |
| `autocomplete` | "0" / "1" | Defaults to "1" (autocomplete mode). |
| `lat`, `lon` | number | Bias results toward this point (does not filter). |
| `returntruegeometry` | bool | Default false. |
| `postcode` | string / list | ≤ 50 comma-separated. Filters `address` + `poi`. |
| `citycode` | string / list | INSEE codes, ≤ 200 comma-separated. Filters `address` + `poi`. |
| `depcode` | string / list | ≤ 10 comma-separated. Filters `address` + `poi`. |
| `type` | enum | `housenumber` / `street` / `locality` / `municipality`. Filters `address`. |
| `city` | string | Filters `address` + `poi` by commune name. |
| `category` | string / list | ≤ 10 comma-separated. Filters `poi`. |
| `departmentcode`, `municipalitycode`, `oldmunicipalitycode`, `districtcode`, `section`, `number`, `sheet` | string | Structured filters for `parcel`. |

Errors: `400 Parse query failed` (malformed input).

### `GET /reverse`
Returns the closest entities to a point or geometry.

| Param | Type | Notes |
|---|---|---|
| `searchgeom` | string (GeoJSON) | `Point`, `LineString`, `Polygon`, or `Circle` (`{"type":"Circle","coordinates":[lon,lat],"radius":m}`). For `address` index only `Polygon`/`Circle` allowed. Bounding rectangle ≤ 1000 m. |
| `lat`, `lon` | number | If `searchgeom` is set, used for ordering. Otherwise, used as the search point (back-compat path — implicit circle). |
| `index`, `limit`, `type`, `postcode`, `citycode`, `depcode`, `category` | — | Same semantics as `/search`. |

### Response shape (both endpoints)
```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": { "type": "Point", "coordinates": [lng, lat] },
      "properties": {
        "label": "2 Rue de la Paix 75002 Paris",
        "score": 0.964,
        "housenumber": "2",
        "id": "75102_6998_00002",
        "name": "2 Rue de la Paix",
        "postcode": "75002",
        "citycode": "75102",
        "city": "Paris",
        "district": "Paris 2e Arrondissement",
        "context": "75, Paris, Île-de-France",
        "type": "housenumber",
        "importance": 0.605,
        "depcode": "75",
        "street": "Rue de la Paix",
        "x": 650894.4, "y": 6863363.33,
        "_type": "address"
      }
    }
  ],
  "query": "..."
}
```

The keys our existing wrapper consumes (`geometry.coordinates`, `properties.{label, postcode, citycode, city}`) are present unchanged.

---

## 4. Synchronous bulk geocoding — `POST /search/csv` and `POST /reverse/csv`

The single biggest capability the old wrapper missed. **One HTTP round trip handles up to 200 000 rows.** The HTTP response holds the result CSV inline (no polling).

### Request (multipart/form-data)

| Field | Type | Required | Notes |
|---|---|---|---|
| `data` | file (binary) | yes | UTF-8 CSV. ≤ 50 MB and ≤ 200 000 rows. |
| `columns` | array of string | recommended | Which CSV columns to concatenate as the query. **If omitted, all columns are concatenated — rarely what you want.** |
| `indexes` | array of string | recommended | Subset of `address` / `poi` / `parcel`. |
| `citycode` | string | optional | Name of the CSV column holding the INSEE filter. |
| `postcode` | string | optional | Name of the CSV column holding the postcode filter. |
| `type` | string | optional | Name of the CSV column holding `housenumber`/`street`/`locality`/`municipality`. |
| `category` | string | optional | POI category column. |
| `lon`, `lat` | string | optional | For result-bias / reverse mode: names of the CSV columns holding coordinates. |
| `departmentcode`, `municipalitycode`, `oldmunicipalitycode`, `districtcode`, `section`, `sheet`, `number` | string | optional | Parcel-index filter column names. |
| `result_columns` | array of string | optional | Which `result_*` columns to keep. Default = all. |

### Response (`200 text/csv`)
The input CSV is returned verbatim with **appended `result_*` columns** (the set we request via `result_columns`, or all of them).

**Address-mode result columns** (with `indexes=address`):
- `result_label`, `result_score`, `result_type`, `result_id`
- `result_housenumber`, `result_name`, `result_street`
- `result_postcode`, `result_city`, `result_citycode`
- `result_oldcitycode`, `result_oldcity`, `result_district`
- `result_context`
- `latitude`, `longitude` (forward mode)
- `result_latitude`, `result_longitude`, `result_distance` (reverse mode)

**Bulk-only columns** (always present in batch output):
- `result_score_next` — second-best score, useful for ambiguity detection.
- `result_index` — which index matched (`address` / `poi` / `parcel`).
- `result_status` — **always check this**:
  - `ok` — found
  - `not-found` — geocoded, no match
  - `skipped` — required filter columns missing or invalid for this row
  - `error` — server-side error during this row's geocoding

**Errors:**
- `400 { code, message }` — request or CSV-file invalid (e.g. malformed CSV, oversize, unknown column reference).

---

## 5. Asynchronous bulk geocoding — `/async/projects/*`

Stateful, multi-step workflow for jobs too big for the sync CSV mode. Up to **1 GB** with a Géoplateforme account; **50 MB** anonymous.

### Lifecycle states (`Project.status`)
`idle` → `waiting` → `processing` → `completed` (or `failed`).
Terminal-but-recoverable: `completed` and `failed` can be reset back to `idle` via `POST /reset`.

### Endpoints (all under `/async/projects/...`)

| # | Method | Path | Purpose | Auth |
|---|---|---|---|---|
| 1 | `POST` | `/async/projects` | Create project. Optional `Authorization: Bearer <user-token>` + `X-Community: <id>` for higher quotas. Response includes `id` + `token` (the **project token** — saved, used by every subsequent call, only returned once). | Bearer (optional) |
| 2 | `PUT` | `/async/projects/{id}/pipeline` | Set processing params (`operation: search|reverse`, `indexes`, `columns`, column-name mappings — same shape as the sync CSV multipart fields). | Project token |
| 3 | `PUT` | `/async/projects/{id}/input-file` | Upload the input file as `application/octet-stream`. Optional `Content-Length` + `Content-Disposition: filename=…`. | Project token |
| 4 | `POST` | `/async/projects/{id}/start` | Move from `idle` → `waiting`. Requires both pipeline + input-file already set. | Project token |
| 5 | `GET` | `/async/projects/{id}` | Poll. Returns `Project` with `status` + `processing.{step, validationProgress, geocodingProgress, validationError, geocodingError, globalError, startedAt, finishedAt, heartbeat}`. | Project token |
| 6 | `GET` | `/async/projects/{id}/output-file/{file-token}` | Download result. The `file-token` is on `Project.outputFile.token` — distinct from the project token. | None (token is in URL) |
| — | `POST` | `/async/projects/{id}/abort` | Cancel a `waiting` or `processing` project — returns to `idle`. | Project token |
| — | `POST` | `/async/projects/{id}/reset` | Reset a `completed` or `failed` project — returns to `idle`. | Project token |
| — | `DELETE` | `/async/projects/{id}` | Delete (`idle`, `completed`, `failed` only). | Project token |
| — | `GET` | `/async/projects/{id}/input-file/{file-token}` | Re-download the source you uploaded. | None (token in URL) |

Output format: CSV (default, same columns as the sync batch above) or GeoJSON — configurable on the pipeline step.

When the project was created authenticated, the **user receives an email** on success/failure. For a library this is incidental — caller code still polls.

### Error codes specific to this path
- `201 Created` on project create (note: not 200).
- `204 No Content` on delete.
- `401` — invalid token (user-token on create, project-token elsewhere).
- `403` — action not allowed in current state (e.g. delete while `processing`, modify after `start`).
- `404` — project / file token not found.

---

## 6. Error codes — summary cheatsheet

| Code | Where | Meaning | Client behaviour |
|---|---|---|---|
| `400` | `/search`, `/reverse` | Parse query failed (bad params). | Surface to caller; do not retry. |
| `400` | `/search/csv`, `/reverse/csv` | Bad CSV (encoding, columns reference) or oversize. Body: `{code, message}`. | Surface; do not retry. |
| `400` | `/async/projects/{id}/input-file` | Oversize / corrupt upload. | Surface; do not retry. |
| `401` | `/async/projects/*` | Invalid auth token. | Surface — usually a config error. |
| `403` | `/async/projects/*` | Operation not allowed in current state. | Inspect `status`; do not blind-retry. |
| `404` | `/async/projects/{id}` | Project / file-token unknown. | Surface. |
| `429` | any | Rate limit (50/s). Header `retry-after` (seconds). | Sleep `retry-after`, retry. |
| `5xx` | any | Server-side error. | Exponential backoff; sub-divide batch if persistent. |
| `504` | any | Upstream timeout. Old wrapper silently swallowed these — new code should retry. | Backoff + retry. |

Per-row failure inside a successful batch (HTTP 200) is signalled by `result_status` ∈ `{not-found, skipped, error}` in the response CSV — see §4.

---

## 7. Practical notes for our wrapper

- **`result_status` is the per-row truth.** A 200 from the batch endpoint does not mean every row succeeded. Always inspect this column and surface it on the per-row dict (e.g. as `found_result`).
- **CSV column names matter.** The multipart `citycode` / `postcode` / `lon` / `lat` fields take **column names from our uploaded CSV**, not values. So our serializer must emit a consistent header row (e.g. `address`, `citycode`) and pass those literal names in the form.
- **Two distinct auth schemes on the async path** — the user Bearer token (only used on create) vs the project token (used on every other call). Easy to confuse.
- **Project token is returned exactly once.** Must be persisted to disk before any other call to `/async/projects/{id}/*` (the wrapper should hand it back to the caller in the iterator's state so it can be saved/reloaded).
- The async path's status polling is via the project token; the file download is via a *separate* file-token. Both come from `GET /async/projects/{id}`.
