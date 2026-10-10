# ADR-0013: Streaming local Parquet snapshot publication

- Status: Accepted
- Date: 2026-10-09

## Context

The durable refresh-run foundation can coordinate work but previously had no executor capable of producing analytical data. A snapshot may contain millions of rows, including an empty result, so schema inference from the first row and full-result materialization are unsafe. Publication also spans two systems that cannot share one transaction: filesystem object storage and metadata PostgreSQL.

Production will use private S3-compatible storage, but a local adapter is needed to prove streaming, type fidelity, immutable naming, validation, and atomic metadata activation before adding remote multipart upload.

## Decision

- Recompile the structured visual definition and execute it inside a read-only, deadline-bound source transaction through a named Psycopg server-side cursor.
- Fetch at most the configured batch size, default 50,000 rows, and convert each batch directly into one Arrow `RecordBatch`. Never construct pandas dataframes or a table containing the complete result.
- Build a fixed Arrow schema from the revision-bound validation artifact before fetching rows. Preserve PostgreSQL type names in Arrow field metadata and durable snapshot schema metadata.
- Snapshot schema version 2 uses UUID-derived physical Parquet column names and records their logical mapping. This avoids collisions with analytical-engine technical columns while keeping the public dataset schema stable.
- Initially support booleans, integer and floating types, bounded decimal types, unconstrained numeric as exact text, character/text/UUID, dates, timestamps, time without zone, binary, JSON/JSONB as canonical JSON text, interval, network addresses, MAC addresses, XML, and money as text. Fail arrays, ranges, composites, extension types, and decimals above Arrow precision with `unsupported_source_type`.
- Incrementally write Parquet 2.6 with Zstandard compression, statistics, and page checksums. Empty results still produce a typed valid Parquet file.
- Write to a unique staging path under the configured storage root. After close, verify schema and row count through Parquet metadata, compute byte size and SHA-256, fsync the file, and atomically rename it to a UUID-based immutable relative object key.
- Only after the complete object exists, transactionally insert snapshot metadata, publish the dataset version, activate the new snapshot, mark any previous active snapshot superseded, create a new editable draft, and complete the refresh run.
- Persist relative object keys rather than host-specific absolute paths. Audit only identifiers, counts, and stable safe failure codes.
- Retain superseded files until an explicit asynchronous cleanup job exists. An object created immediately before a metadata failure is an unreferenced orphan and is likewise handled by future garbage collection.

## Consequences

- Memory use is bounded by one source batch plus one Arrow batch rather than total result size.
- Empty datasets and nullable first batches retain deterministic types.
- Readers cannot observe a partial snapshot or metadata pointing at an incomplete file.
- Filesystem and PostgreSQL cannot commit atomically; orphan cleanup is required, but publication failure cannot replace the active snapshot.
- The local adapter is suitable only for one-node development and tests. S3-compatible storage remains mandatory for horizontally scaled production.
- Snapshot rows are durable and are read by the bounded DuckDB query slice defined in ADR-0014.

## Alternatives considered

- Building one Arrow table or pandas dataframe was rejected because memory would scale with the complete result.
- Inferring schema from the first batch was rejected because empty results and leading nulls would be ambiguous.
- Publishing metadata before closing and validating Parquet was rejected because readers could observe partial or corrupt data.
- Deleting the previous file immediately was rejected because future concurrent readers require a grace period and explicit cleanup lifecycle.
- Storing absolute filesystem paths was rejected because metadata must remain portable across worker hosts and storage adapters.
