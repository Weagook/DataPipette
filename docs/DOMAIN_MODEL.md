# Domain model

## Vocabulary

### User

A human identity with a local account. A user has one system role and may belong to groups.

### Service account

A non-human identity used by one external integration. It has a `viewer` or `analyst` role, active/disabled lifecycle, group membership or direct grants, and one or more independently revocable API tokens. It cannot authenticate with a password or become an administrator.

### API token

An opaque bearer credential owned by one service account. Its plaintext is returned exactly once; metadata stores only a SHA-256 digest plus a display name, closed scope set, optional expiry, revocation timestamp, creation metadata, and last-used timestamp. Multiple tokens support rotation. The initial `dashboards:read` scope permits only granted dashboard reads and saved aggregate-item execution and never expands the account's role or ACLs.

### Group

A named collection of users or service accounts used to grant access consistently.

### Role

A coarse capability set:

- `Admin` manages the installation and has access to every resource.
- `Analyst` creates and manages analytical resources within granted scope.
- `Viewer` reads explicitly shared dashboards and other allowed resources.

A role answers “what kind of action may this identity perform?” An ACL grant answers “on which resource may it perform that action?”

### ACL grant

An additive permission assigned to one user or group for one resource. New resources are private to their owner and administrators. MVP has no explicit deny rule.

Implemented dataset permissions are `view_rows`, `export`, and `manage`. An active analyst receives the union of direct and group grants; the dataset owner and administrators receive all three. `Viewer` is a hard role ceiling and never permits detailed rows, exports, or grant management. Creating an export requires both `view_rows` and `export`. `manage` allows its holder to delegate and revoke dataset grants.

### Connection

An administrator-managed PostgreSQL data source. It stores encrypted credentials and metadata required to connect. A connection is never itself a query result.

### Connection grant

Administrator-managed permission for an active analyst or group to use a connection. Exact scopes are every database-visible object (`all`), one schema, or one relation. Effective visibility is the union of direct and current group grants, intersected with the configured PostgreSQL role's privileges. Administrators bypass grants; viewers cannot use source connections. Connection access permits source discovery and dataset authoring but does not grant access to another owner's dataset or its snapshots.

### Dataset

A virtual, named analytical resource backed by one PostgreSQL connection. It contains:

- ownership and ACL metadata;
- a published definition;
- an optional draft definition;
- a typed result schema;
- table display settings such as column order and formats;
- refresh schedule and state; and
- a reference to the active snapshot.

A dataset is not a physical table and does not depend on another dataset.

### Dataset definition

One of two forms:

- **Visual definition:** selected source columns, table aliases, explicit `INNER JOIN` or `LEFT JOIN` relationships, and other non-aggregate row-level operations represented as structured data.
- **SQL definition:** one advanced read-only `SELECT` or `WITH` statement.

Generated SQL is an execution artifact of a visual definition, not its source of truth.

Visual definitions are schema-versioned JSON documents. Source relations, joins, and output fields have stable UUIDs; aliases, labels, display formats, and output names may change without changing those identities. Version 1 contains exactly one source relation and no joins. Version 2 contains one to ten uniquely aliased relations connected as an ordered acyclic tree by explicit `INNER JOIN` or `LEFT JOIN` nodes with equality-only column pairs. The first relation is the root and every later relation is introduced exactly once. Metadata PostgreSQL stores the complete document in JSONB and the application replaces it atomically rather than updating nested paths.

Foreign-key relationships discovered from PostgreSQL are advisory join suggestions. They are not persisted separately and never mutate a definition until the client explicitly saves a corresponding join node. Manual conditions use the same bounded typed structure.

SQL definitions are schema-versioned JSON documents with `kind: sql`, the original trimmed statement, and stable output-field UUIDs plus names and display settings. The statement is one bounded PostgreSQL-dialect `SELECT`/query expression, may use read-only CTEs, and must schema-qualify every base relation. Structural parsing rejects multiple statements, DDL/DML, data-modifying CTEs, `SELECT INTO`, row locking, external parameters, and cross-database names. The original text is preserved for editing; a canonical execution form and source-relation set are derived whenever the document is loaded.

An analyst-created dataset is private to its owner and administrators. The owner can edit and execute it only while every referenced relation remains in their effective connection scope. Scope revocation blocks later source access but does not delete the definition or an existing published snapshot.

### Draft and published versions

The published definition and snapshot serve readers. Editing creates or updates a draft. Publication succeeds only when validation, impact analysis, refresh, storage, and activation complete successfully.

Drafts are mutable through optimistic revision checks. A future published version is immutable; editing it creates or resumes a separate draft instead of modifying the definition used by readers.

Published and superseded versions form immutable, owner-visible history. The current draft is compared with the active published version only after validation of the exact revision. Schema impact uses stable field UUIDs to distinguish additions, removals, renames, PostgreSQL type/nullability drift, unavailable result fields, and compatible display/source changes. Reports are derived rather than persisted; chart dependency collections remain empty until real chart resources exist.

### Draft validation

An explicit, derived artifact bound to one draft revision. It records structural validity, PostgreSQL-formatted output types and nullability, safely compiled SQL, a compact planner estimate, stable findings, and validation time. SQL validation also requires declared output-field names to exactly match PostgreSQL result columns. It contains neither source rows nor a raw execution plan. Editing the definition invalidates the artifact. A missing relation or field is an ordinary invalid result; source connectivity failure is operational failure and does not overwrite the previous artifact.

### Draft preview

A transient, bounded read of source rows for the exact current validated revision. It is not a snapshot, version, or persistent report result. Preview may use a `valid_with_warnings` draft because its row count, encoded bytes, statement duration, and client memory are independently bounded. Its row order is unspecified until the request model supports an explicit sort.

### Snapshot

An immutable Parquet representation of one successful dataset execution. A dataset exposes one active snapshot. The previous snapshot remains readable until a replacement is fully published, then becomes eligible for cleanup.

Implemented metadata includes the published dataset-version and refresh-run identities, durable storage backend (`local` or `s3`), relative immutable object key, state, row and byte counts, SHA-256 checksum, typed schema, lifecycle timestamps, and an optional physical-purge timestamp. Local files are development/test objects. S3-compatible objects live in one configured private bucket and are verified after upload and before remote reads. Superseded objects remain through a cleanup grace and while any queued, running, or unexpired export references them; cleanup removes the private object but retains metadata history.

### Refresh run

A background execution that reads the source, writes a candidate snapshot, determines its schema and statistics, and attempts publication. Runs preserve operational history even though old snapshot data is deleted.

Implemented foundation states:

```text
queued -> running -> succeeded
   |        |----> failed
   |        |----> queued (bounded transient retry)
   |        `----> cancelled
   `-------------> cancelled
```

At most one run executes for a dataset and at most one additional run is coalesced as pending.

Each manual request supplies an idempotency key. Metadata PostgreSQL stores its digest and mapping to the durable run; Redis carries only the run identifier. Dispatch and execution attempt counts are separate. A broker failure leaves the queued intent retryable, and duplicate task delivery cannot re-execute a running or terminal run while its renewable execution lease is fresh. An expired running lease is reclaimable within the same attempt limit, so a lost worker does not leave permanent `running` state. Explicit transient source/storage failures return the same run to `queued` with a durable retry due time and bounded exponential backoff. A newer queued successor prevents retry of an older run.

Queued cancellation is immediate. Running cancellation records durable intent and becomes terminal cooperatively at safe execution boundaries or atomically before publication. Repeated cancellation of a cancelled run is idempotent. Success references the newly active snapshot; failure or cancellation preserves the previous active snapshot.

Refresh runs record whether they were requested manually or by a schedule. A scheduled run references its originating schedule and always targets the active published version; it never executes the separately editable draft.

### Refresh schedule

One optional owner-managed cadence for a dataset. It is either a fixed interval from 5 minutes through 7 days or one daily wall-clock minute in the installation timezone. Disabled schedules retain their configuration with no next occurrence. Enabled schedules store the next UTC occurrence and the last trigger time. The scheduler advances missed work to one next future occurrence, creates or coalesces a durable refresh run, and does not cancel already queued or running work when disabled.

### Dataset freshness

A read-only projection derived from the active snapshot and bounded refresh-run history. Its data state is `not_published`, `refreshing`, `ready`, `stale`, or `failed`. `stale` means an active snapshot remains readable after a later refresh failure; a failed first publication has no readable snapshot and is therefore `failed`, not stale. A queued or running retry is `refreshing` while retaining the latest relevant safe error until successful snapshot activation.

### Dataset table view

The official UI representation of detailed rows in the active snapshot. It is not a separate `Report` entity. Temporary filters, pagination, and sorting apply to the current request. Exports may reuse them.

The implemented exploration descriptor exposes the active published field order, labels, visibility, formats, type-compatible filter operators, and visible default projection to callers with `view_rows`. The query contract selects fields by stable UUID, applies typed temporary predicates, and uses an encrypted cursor tied to one immutable snapshot and query shape. Multiple values from an Excel-like UI become an explicit `in` list. Changing filters, sorting, projection, or the active snapshot invalidates the cursor rather than mixing pages from different result sets. Draft display edits do not affect readers until successful publication.

### Chart

A persistent analytical query and visualization over one dataset snapshot. It stores dimensions, measures, typed filters, groupings, sorting, limits, chart type, and presentation options.

A chart is implemented as a private owner-bound, schema-versioned definition over one dataset. Stable dataset field UUIDs identify its dimension, measures, and filters; stable measure UUIDs identify aggregate result columns. Updates replace the complete definition under an opaque revision ETag. Direct execution re-checks `view_rows`, resolves the current active published snapshot, and runs a bounded aggregate DuckDB query over Parquet without touching source PostgreSQL.

The implemented definition supports `bar`, `line`, `pie`, `donut`, and `kpi`, zero or one dimension, and `COUNT`, `COUNT DISTINCT`, `SUM`, `AVG`, `MIN`, and `MAX`. Definition version 2 can bucket a date or timestamp dimension by hour, day, ISO week, month, quarter, or year. Version 1 remains readable without buckets. Timestamp-with-time-zone values are converted to the installation timezone before bucketing; timestamp-without-time-zone values are treated as installation-local, while dates are never timezone-converted. A normalized dependency row is stored for every referenced field so dataset schema impact can identify and block breaking changes. Chart deletion is not implemented until archival and dashboard-reference behavior is accepted.

A chart becomes `invalid` rather than being deleted when a forced dataset change breaks its column dependencies.

### Dashboard

A named layout of chart references. Charts may use different datasets and load independently. A dashboard does not join or calculate across datasets.

The implemented dashboard is a private-by-default, owner-bound, schema-versioned document with a fixed 12-column grid. Each item has a stable UUID, one owned chart reference, and bounded integer geometry. A definition may contain up to 50 unique non-overlapping items; an empty layout is valid. Complete replacement uses an opaque revision ETag, and no-op replacement preserves the current revision.

The JSONB definition is the authoritative editable document. Normalized chart dependency rows enforce referential integrity and make future chart lifecycle analysis possible without parsing every layout. Administrators may manage another owner's dashboard, but its items must still reference that owner's charts. Additive direct-user and group grants provide view-only access; ownership or administration is still required to edit the layout or grants.

An implemented dashboard-item query resolves one chart solely by the stable item UUID in the current saved definition. Each item is requested independently, so unrelated charts can load, fail, retry, and be cancelled separately. The response carries the dashboard revision alongside the chart and snapshot metadata, allowing clients to reject a late result after a layout replacement. Owner, administrator, and shared-recipient requests use the same saved definition and bounded aggregate executor. Dashboard-context execution does not grant dataset rows or export and cannot be used with an arbitrary chart; direct chart routes retain their independent authorization.

### Export job

An asynchronous request to write a filtered dataset table view to CSV or XLSX. The resulting private file expires after 24 hours. Download URLs expire after five minutes and may be reissued while the file exists.

Implemented states:

```text
queued -> running -> succeeded
                  -> failed
```

Each job is bound to the active immutable snapshot at creation and stores selected field UUIDs plus the typed temporary filters and sorts needed to reproduce the request. Metadata PostgreSQL is authoritative; Redis carries only the export UUID. A successful job records its durable storage backend, immutable private object key, media metadata, size, checksum, and eventual purge time. The completed file becomes logically unavailable 24 hours after success. Automatic cleanup removes it after an additional delivery grace while retaining job history. Five-minute encrypted download capabilities can be reissued before expiry without regenerating the file. Local capabilities stream through DatePipette; S3-backed capabilities resolve to equally short-lived presigned private-object URLs after object verification.

### Audit event

An append-only record of a security-sensitive or governance-relevant action. It records the actor, action, resource, result, request correlation, and redacted metadata.

The administrator read projection preserves human-user, service-account, anonymous, and system actor types. It is ordered newest first by occurrence time and UUID, supports bounded opaque-cursor pages and exact governance filters, and has no update or delete operation. Event details are producer-defined safe metadata rather than captured request payloads or business data.

### Query Safety policy

The singleton installation-wide planner policy contains warning and block thresholds for estimated rows, planner cost, and join count plus advisory scan, sort, and statistics-age thresholds. Absence of a stored row means revision-zero installation defaults. Complete administrator replacement creates or increments the metadata revision under an ETag and produces an audit event.

Each new dataset validation stores the exact policy revision used. A privileged override is embedded in that immutable validation artifact with actor, reason, occurrence time, and stable blocked finding codes; it is not a reusable permission or a global bypass.

## Relationships

```text
User / ServiceAccount --< GroupMembership >-- Group
User / Group          --< ACLGrant >-------- Resource
ServiceAccount        --< ApiToken

Connection            --< ConnectionGrant
Connection            --< Dataset
Dataset               --1 ActiveSnapshot
Dataset               --< RefreshRun
Dataset               --< Chart
Dashboard             --< DashboardItem >-- Chart
Dataset               --< ExportJob

Actor                 --< AuditEvent
```

## Ownership and deletion

Ownership is separate from access grants. Deletion, archival, ownership transfer, and dependency behavior require a dedicated decision before destructive endpoints are implemented. Until then, implementations should prefer reversible archival and must not silently cascade-delete analytical resources.

## Time semantics

- Audit and operational timestamps are stored in UTC.
- One administrator-configured IANA timezone applies to the installation.
- PostgreSQL `date` values are not timezone-converted.
- `timestamp with time zone` values are displayed and bucketed in the installation timezone.
- MVP interprets `timestamp without time zone` as already expressed in the installation timezone.
