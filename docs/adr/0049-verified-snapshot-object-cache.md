# ADR-0049: Verified local cache for snapshot objects

- Status: Accepted
- Date: 2026-10-29

## Context

Interactive table, chart, and dashboard-item queries and background exports read a snapshot through the object storage `materialize` operation. With S3-compatible storage, every request downloaded the complete Parquet object into a temporary file, recomputed its SHA-256, ran DuckDB, and deleted the file. The 2026-10-19 benchmark identified this as the dominant interactive cost: about one second per request for a 130 MB snapshot, held inside an admission slot, so five or more clients mostly received capacity responses.

Published snapshot objects are immutable, and their size and SHA-256 are recorded in metadata.

## Decision

- `S3ObjectStorage` keeps a content-addressed local cache of snapshot objects, keyed by the recorded SHA-256. A miss downloads the object into the cache volume, verifies its size and SHA-256 exactly as before, and atomically renames it into place. A hit checks only the recorded size, which matches the existing local-storage read path, and records the use by updating the file's modification time.
- Only objects with a recorded size and a well-formed SHA-256 that fit in the budget are cached. Everything else, and any cache-volume failure such as a full disk, falls back to the previous temporary download. A failed verification is never cached and is not retried through the fallback.
- Concurrent first readers in one process share one download. Different processes may download the same object concurrently; the content-addressed atomic rename makes that harmless.
- The cache is bounded by `DATAPIPETTE_SNAPSHOT_CACHE_MAX_MB` (default 4,096; 8,192 in the Compose topology; `0` disables it). After each insert, least-recently-used entries are deleted until the cache fits, but entries used in the last five minutes are kept even if that temporarily exceeds the budget, so a file is not removed between being handed to a reader and DuckDB opening it. Interactive queries are limited to 60 seconds, and an opened file stays readable after deletion on Linux.
- Compose mounts one `snapshot-cache` named volume into the API and worker, so the cache survives restarts and is shared by interactive reads and exports. The cache is never placed on the tmpfs snapshot work directory.

## Consequences

- On the reference node with the 229 MB 5M × 50 snapshot in local MinIO, a warm-cache point lookup takes 57 ms instead of 1.5 s, a filtered sort 204 ms instead of 1.7 s, and a 50-column page 772 ms instead of 2.3 s. See the [2026-10-29 report](../../benchmarks/OPTIMIZATION_2026-10-29.md).
- Admission slots are now held for the query rather than for a full download, so the same concurrency limit admits more work.
- The cache volume holds copies of business data. It is private to the backend containers, regenerable, and deliberately excluded from backups.
- Superseded snapshots remain in the cache until LRU eviction removes them; authorization still comes from metadata on every request, so a cached copy grants no access.
- Corruption of a cached file after verification is detected only by size; the cache trusts its own volume as much as the local storage backend trusts its directory.

## Alternatives considered

- **DuckDB ranged reads directly from S3 (`httpfs`):** rejected for now because it downloads a runtime extension, gives DuckDB storage credentials, and gives up per-object SHA-256 verification; it remains an option for snapshots much larger than any local cache.
- **Caching in Redis or memory:** rejected because snapshots are hundreds of megabytes and the API containers' work directory is already memory-backed tmpfs.
- **Verifying the SHA-256 on every hit:** rejected because hashing the whole object was a large share of the original per-request cost.
