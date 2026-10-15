# ADR-0015: Snapshot-bound background exports

- Status: Accepted
- Date: 2026-10-10

## Context

Filtered dataset results can contain millions of rows and cannot be built safely in an API response. An export must remain reproducible if a refresh publishes a new active snapshot while a job is queued or running. CSV/XLSX files contain business data, spreadsheet programs may interpret attacker-controlled strings as formulas, and a reusable download endpoint must not make private storage public.

## Decision

- Represent every export as a durable metadata job in `queued`, `running`, `succeeded`, or `failed` state. The API records intent before dispatching a UUID-only Celery message on the `export` queue.
- Require an `Idempotency-Key` scoped to actor and dataset. A retry returns and, when necessary, redispatches the same saved job.
- Resolve and validate the selected field UUIDs, typed temporary filters, and sorts against the active published schema at creation. Persist the query document and bind the job to that exact immutable snapshot ID.
- Generate files in a worker with DuckDB projection/predicate pushdown, deterministic sorting, bounded memory, temporary disk, threads, fetch batches, and a maximum row count. Write a candidate file and atomically rename it only after success.
- Support UTF-8-with-BOM CSV and macro-free XLSX. Prefix string cells beginning with `=`, `+`, `-`, `@`, tab, or carriage return with an apostrophe. XLSX keeps boolean, integer, floating, date, time, and timestamp cells typed where supported; precision-sensitive decimals remain text.
- Store artifacts under generated private object keys. The current local adapter is for development and single-node testing; production storage remains S3-compatible.
- Mark successful exports unavailable after 24 hours from completion. Issue an AES-GCM encrypted/authenticated download capability for at most five minutes after administrator authorization. A new capability may be issued without regenerating an unexpired file.
- Keep creation, history, and link issuance administrator-only until dataset ACLs exist. Capability download requires the unexpired opaque token rather than a browser session.
- Audit lifecycle identifiers, counts, sizes, format, and safe errors only. Never place rows, filter values, idempotency plaintext, token plaintext, SQL, or absolute paths in audit events.

## Consequences

- Export content is stable across later snapshot publication and failures cannot affect the active dataset.
- CSV/XLSX generation does not load the complete result into Python memory.
- Metadata PostgreSQL is authoritative; Redis loss does not erase a requested job.
- Formula-injection defenses slightly alter dangerous leading string values by adding a visible spreadsheet escape marker.
- Logical expiry is enforced immediately by the API. Physical deletion is intentionally deferred to the cleanup workflow, so expired local files may remain inaccessible on disk until cleanup runs.
- The deferred physical cleanup workflow was subsequently implemented by [ADR-0023](0023-analytical-object-lifecycle-cleanup.md).
- XLSX cannot exceed its worksheet row limit; the configured export limit also bounds CSV.
- [ADR-0039](0039-governed-background-analytical-work.md) subsequently makes export budgets revisioned administrator policy and shares distributed admission with refresh attempts.

## Alternatives considered

- Synchronous downloads were rejected because request timeouts and process memory would scale with result size.
- Re-querying whichever snapshot is active when the worker starts was rejected because it makes a queued request non-reproducible.
- Reusing interactive pagination cursors was rejected because it would repeatedly reopen DuckDB and assemble a large artifact through the API-shaped response path.
- Public filesystem paths and long-lived bearer URLs were rejected because exports contain private business data.
