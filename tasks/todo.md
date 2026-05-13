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
- [x] Commit phase-2 (`1012866`)

## Phase 3 — Migrate `api_gps.py` (unitary GET, behaviour-preserving)
- [x] Switch base URL to `https://data.geopf.fr/geocodage/search` and `/reverse`, `index=address` on both
- [x] Fix `len(postal_address) < 4` bug — early return, no HTTP call
- [x] `get_address_from_gps`: `'long'` → `'lng'` (KeyError on stale callers, per user direction)
- [x] Harden reverse non-200 path: `_safe_text()` helper, no blind `.json()`
- [x] `print()` → `logging.getLogger(__name__)` + `info/warning/error/exception`
- [x] `batch_gps_coordinates` refactor: shared `ClientSession`, `asyncio.Semaphore(20)`, `asyncio.gather` for order, tqdm via `pbar.update()` in the bounded coroutine
- [x] Drop `numpy` import + `from math import ceil`
- [x] Ctor extended (back-compat): optional `session`, `base_url`, `max_concurrent`
- [x] `_read_json_response` becomes sync (it had no awaits)
- [x] `_session_for()` async-context-manager helper
- [x] All 4 collected pytest tests pass against new URL; `__main__` demo verified live
- [x] Flag pre-existing duplicate `test_batch_gps_coordinates_with_insee` name (out of scope)
- [x] Commit phase-3 (`0a4c400`)

## Phase 4 — Sync CSV batch (`sync_server.csv_geocoder`)

### 4a — implementation + live smoke test
- [x] `EtalabSyncCsvGeocoder.geocode(rows, chunk_rows=200_000)` — async generator, lazy input via `itertools.islice`
- [x] `EtalabSyncCsvGeocoder.reverse_geocode(rows, chunk_rows=200_000)` — symmetric
- [x] CSV builder (`_build_input_csv`) — handles forward (`address,citycode` or `address` only) + reverse (`lon,lat`); `csv.writer` for proper escaping
- [x] CSV parser (`_parse_response_csv` → `_row_to_result`) — maps `result_*` columns to the canonical dict shape (same keys as `api_gps`), plus `result_status` ∈ `{ok, not-found, skipped, error}`
- [x] Multipart POST with `aiohttp.FormData` rebuilt per retry attempt
- [x] Retry: 429 honors `retry-after`, 5xx exponential backoff (cap 60s), 4xx-non-429 raises
- [x] Persistent failure → subdivide chunk in half; at `min_subdivide_rows` give up and yield `result_status="error"` rows
- [x] 3 live smoke tests in `tests/test_sync_csv_geocoder.py` (forward, forward+INSEE, reverse) — all pass
- [x] Full suite (4 old + 3 new) all pass in 2s
- [ ] Commit 4a

### 4b — full mocked unit tests
- [ ] `aiohttp` mocking via `aioresponses` (add to requirements-dev or test-only)
- [ ] Test chunking: 250 rows with chunk_rows=100 → 3 chunks → 3 POSTs
- [ ] Test 429 retry: mock 429 with `retry-after: 1`, then 200 → exactly one sleep, second POST succeeds
- [ ] Test 5xx backoff: mock 500 ×2, then 200 → 2 backoff sleeps, third POST succeeds
- [ ] Test 4xx non-429: mock 400 → `_PersistentBatchFailure` → subdivide → if min already, error rows yielded
- [ ] Test subdivide: mock 500 persistently on chunk size 200, then 200 on each half of 100 → 2 successful sub-POSTs, results in order
- [ ] Test giving up at min_subdivide_rows: mock 500 persistently → `result_status="error"` row per input row, count matches
- [ ] Test result_status mapping: `ok`/`not-found`/`skipped`/`error` from mocked response CSV → correct dict shape
- [ ] Commit 4b

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
