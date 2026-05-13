# Plan — Géoplateforme migration + bulk geocoding (`feature/geopf-migration`)

**Goal:** migrate off the soon-decommissioned `api-adresse.data.gouv.fr` to `data.geopf.fr/geocodage`, fix existing bugs, and add two new bulk geocoders (sync CSV + async projects) so the caller can geocode 27M addresses in reasonable wall-clock time.

**Branch:** `feature/geopf-migration`
**Target version:** `0.0.0.9` (additive — existing public API preserved).

**Library scope reminder:** input is a generic `Iterable[(address, insee)]`, output is an async iterator of result dicts. No Postgres / CSV-file integration baked in — caller wires it up.

---

## Phase 0 — Prep
- [x] Write this plan to `tasks/todo.md`
- [x] Create branch `feature/geopf-migration` off `master`
- [x] Confirm working-tree state (carry the `requirements.txt` comment edit + the untracked `CLAUDE.md` / `tasks/` over)
- [x] Add `.playwright-mcp/` to `.gitignore`

## Phase 1 — API research doc (no code risk)
- [x] Create `api_doc.md` at repo root with: URL migration notice, base URL, all 5 endpoint groups, full param tables, multipart fields, async project workflow, rate limits + file/row caps, auth schemes, response shapes, error-code cheatsheet, practical notes for the wrapper
- [x] Commit phase-1 (`f951a92`)

## Phase 2 — Package skeleton
- [x] Create `etalab_apis/sync_server/__init__.py` (empty)
- [x] Create `etalab_apis/sync_server/csv_geocoder.py` with class stub `EtalabSyncCsvGeocoder`
- [x] Create `etalab_apis/async_server/__init__.py` (empty)
- [x] Create `etalab_apis/async_server/project_geocoder.py` with class stub `EtalabAsyncProjectGeocoder`
- [x] Verify both stubs import cleanly
- [ ] Commit phase-2

## Phase 3 — Migrate `api_gps.py` (unitary GET, behaviour-preserving)
- [ ] Switch base URL to `https://data.geopf.fr/geocodage/search` and `/reverse`
- [ ] Append `index=address` to every search query (keeps current address-only behaviour)
- [ ] Fix `len(postal_address) < 4` bug: assign the "not found" dict and `return` it, skip the HTTP call
- [ ] Fix `get_address_from_gps`: `'long'` → `'lng'` (note: this is a public-surface key change — call out in commit msg)
- [ ] Harden `get_address_from_gps` non-200 path (don't call `.json()` blindly)
- [ ] Replace `print()` calls with module-level `logging.getLogger(__name__)` + `logger.error/warning`
- [ ] Refactor `batch_gps_coordinates`: accept optional shared `aiohttp.ClientSession`, use `asyncio.Semaphore(40)` instead of `np.array_split` chunking, drop the post-call `sleep`
- [ ] Drop `numpy` import + the `from math import ceil`
- [ ] Re-run live integration tests in `tests/test_api_gps.py` — assert they still pass against the new URL
- [ ] Commit phase-3: `refactor: migrate api_gps to data.geopf.fr + fix short-address bug + lng key`

## Phase 4 — Sync CSV batch (`sync_server.csv_geocoder`)
- [ ] Implement `EtalabSyncCsvGeocoder` with:
  - Ctor: `session: ClientSession | None`, `base_url`, `max_concurrent_batches` (chunks can run in parallel within rate limit)
  - `geocode(rows: Iterable[Tuple[str, str | None]], chunk_rows: int = 200_000) -> AsyncIterator[Dict]`
  - Internal: build CSV in-memory per chunk (StringIO), `POST /search/csv` with multipart, parse `text/csv` response, yield rows mapped to the same `{gps, lat, lng, postcode, insee_city_code, city, postal_address, found_result}` shape as unitary
  - Retry: exponential backoff on 429 / 5xx, honor `retry-after`
  - Sub-divide: on persistent failure, halve the chunk and retry each half (recursive, with a minimum chunk size)
- [ ] Implement `reverse_geocode(rows: Iterable[Tuple[float, float]], ...)` (POST /reverse/csv) — symmetric
- [ ] Add unit tests `tests/test_sync_csv_geocoder.py` using `aioresponses` (mocked HTTP — no live calls in this phase's tests; live tests live separately)
- [ ] Commit phase-4: `feat(sync_server): bulk CSV geocoder (POST /search/csv & /reverse/csv)`

## Phase 5 — Async projects (`async_server.project_geocoder`)
- [ ] Implement `EtalabAsyncProjectGeocoder` with:
  - Ctor: `session`, `base_url`, `auth_token: str | None`, `community: str | None`, `poll_interval: int = 30`
  - `geocode(rows: Iterable[Tuple[str, str | None]]) -> AsyncIterator[Dict]`
  - Internal workflow: `POST /async/projects` → upload file part → poll `GET /async/projects/{id}` until status==done → download result → stream-parse CSV → yield dicts
  - Honour `auth_token` via `Authorization: Bearer …` + `X-Community` header when set
  - Surface project-id + result-url on the returned iterator (attribute or via a context object) so caller can resume / re-download
- [ ] Add unit tests `tests/test_async_project_geocoder.py` (mocked HTTP)
- [ ] Commit phase-5: `feat(async_server): async project geocoder (POST /async/projects workflow)`

## Phase 6 — Polish + release prep
- [ ] Remove `numpy` line from `requirements.txt`; ensure `aiohttp`, `tqdm` stay
- [ ] Bump `setup.py` version → `0.0.0.9`, update `install_requires` (drop numpy)
- [ ] Update `CLAUDE.md`:
  - Architecture section: now three packages, three classes, one shared scope rule
  - Remove the "Known rough edges" entry for the `len<4` bug + the `'long'`/`lng` inconsistency (both fixed)
  - Add: new endpoints, where rate limit lives, where retry/sub-divide logic lives
- [ ] Final pass: run `pytest` end-to-end
- [ ] Update this `tasks/todo.md` "Review" section with what shipped vs. what was deferred
- [ ] Commit phase-6: `chore: bump to 0.0.0.9, drop numpy, update CLAUDE.md`

---

## Deferred / not in scope
- Postgres / CSV-file convenience wrappers (caller plumbing — out of scope per [[feedback-library-scope]]).
- Dedup of input addresses (caller's job).
- Resumability across process restarts (caller's job — wrapper just yields; caller can stop/resume by tracking which input rows have a result).
- Lifting the per-IP rate limit via an IGN public-sector request (operational, not code).

## Review (filled at end of work)
_(empty until phase 6 lands)_
