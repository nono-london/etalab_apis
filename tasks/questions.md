# Open questions — Géoplateforme migration + 27M-address batch

Context: caller has ~27M addresses to geocode. The current unitary-GET wrapper would take ~6 days at the rate-limit ceiling, so we need to migrate to `data.geopf.fr/geocodage` and add bulk-CSV support. Several design decisions depend on operational facts only the user knows.

---

## Q1 — Where do the 27M addresses live now, and where do results need to land?
Status: **open**

This determines whether the new method takes `List[str]`, an iterable, a file path, or a DB cursor — and whether results stream back to a sink or are returned in memory.

Likely candidates given the global CLAUDE.md hint about a `france_aides_datalake` Postgres database:
- Source = a `raw` / `clean` Postgres table, results UPSERT'd back into a column on that table?
- Or source = a CSV file on disk, results written next to it?
- Or something else?

A 27M-row in-memory list of result dicts is ~5–10 GB of Python objects. Whatever the answer, the wrapper must not buffer all results in memory.

---

## Q2 — Is this a one-off ingestion or recurring?
Status: **open**

- One-off: a CLI script with checkpoint-to-disk and resume-from-where-it-died is fine; the wrapper just needs to expose the right primitive.
- Recurring (e.g., new addresses arrive weekly): the wrapper itself should own resumability — track which rows have a result, only resubmit the rest.

---

## Q3 — Do you have (or can you get) a Géoplateforme auth token?
Status: **open**

Affects whether **Option C (async projects)** is on the table:
- Anonymous async projects: 50 MB file, `concurrency=1` — barely better than sync CSV.
- Authenticated with an `X-Community` header: bigger file (up to 1 GB), parallel processing on the server side, and the IGN can lift the per-IP rate limit on request for public-sector use cases.

If yes/maybe → worth wrapping the async workflow. If no → stick to Option A + B.

---

## Q4 — What time budget is acceptable for the 27M job?
Status: **open**

- Hours (run during the day, want progress) → Option B with parallel batches + progress UI.
- Overnight is fine → Option B sequential, or Option C async with a notification.
- Multi-day / background → Option C async projects (server does the work, no babysitting).

---

## Q5 — Should the existing `get_gps_coordinates(single_address)` method keep its current shape?
Status: **open**

The current method is used as a library by callers elsewhere (the package is published-style with version bumps). Migrating its base URL to `data.geopf.fr/geocodage` is forced anyway (old URL is past sunset), but I want to confirm:
- Method names and return-dict keys stay identical (`gps`, `lat`, `lng`, `postcode`, `insee_city_code`, `city`, `postal_address`, `found_result`)? → safe v0.0.0.9 patch.
- Or are you OK with renames now that we're rewriting it? → v0.1.0, callers update with us.

---

## Q6 — Dedup before sending?
Status: **open**

27M *addresses* often contain many duplicates (e.g., same building, same INSEE+street). Dedup before submitting can cut server load 2–10× depending on the dataset shape. Should the wrapper do this automatically (hash on `(address, insee)` → submit unique → fan results back out) or leave it to the caller?

---

## Q7 — Retry policy on partial CSV-batch failure?
Status: **open**

A 200k-row sync CSV upload that 504s or returns 429 mid-batch is a real risk. Options:
- Retry the whole 200k-row batch with backoff.
- Subdivide on failure (split in half, retry each half).
- Mark the batch as failed in a manifest and continue, hand-fix later.

Preference?
