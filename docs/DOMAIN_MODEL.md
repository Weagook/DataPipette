# Domain model

## Vocabulary

### User

A human identity with a local account. A user has one system role and may belong to groups.

### Service account

A non-human identity used by one external integration. It has a role, group membership or direct grants, and one or more independently revocable API tokens.

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

### Connection

An administrator-managed PostgreSQL data source. It stores encrypted credentials and metadata required to connect. A connection is never itself a query result.

### Connection grant

Permission to use a connection. The grant exposes either all objects visible to the PostgreSQL role or an allowlist of schemas and tables. Effective visibility is the union of a principal's direct and group grants.

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

Visual definitions are schema-versioned JSON documents. Source relations and output fields have stable UUIDs; labels and output names may change without changing those identities. The first implemented definition version contains exactly one source relation, one or more selected fields, display metadata, and no joins. Metadata PostgreSQL stores the complete document in JSONB and the application replaces it atomically rather than updating nested paths.

### Draft and published versions

The published definition and snapshot serve readers. Editing creates or updates a draft. Publication succeeds only when validation, impact analysis, refresh, storage, and activation complete successfully.

Drafts are mutable through optimistic revision checks. A future published version is immutable; editing it creates or resumes a separate draft instead of modifying the definition used by readers.

### Snapshot

An immutable Parquet representation of one successful dataset execution. A dataset exposes one active snapshot. The previous snapshot remains readable until a replacement is fully published, then becomes eligible for cleanup.

### Refresh run

A background execution that reads the source, writes a candidate snapshot, determines its schema and statistics, and attempts publication. Runs preserve operational history even though old snapshot data is deleted.

Suggested states:

```text
queued -> running -> succeeded
                  -> failed
                  -> cancelled
```

At most one run executes for a dataset and at most one additional run is coalesced as pending.

### Dataset table view

The official UI representation of detailed rows in the active snapshot. It is not a separate `Report` entity. Temporary filters, pagination, and sorting apply to the current request. Exports may reuse them.

### Chart

A persistent analytical query and visualization over one dataset snapshot. It stores dimensions, measures, typed filters, groupings, sorting, limits, chart type, and presentation options.

A chart becomes `invalid` rather than being deleted when a forced dataset change breaks its column dependencies.

### Dashboard

A named layout of chart references. Charts may use different datasets and load independently. A dashboard does not join or calculate across datasets.

### Export job

An asynchronous request to write a filtered dataset table view to CSV or XLSX. The resulting private file expires after 24 hours. Download URLs expire after five minutes and may be reissued while the file exists.

Suggested states:

```text
queued -> running -> ready -> expired
                  -> failed
```

### Audit event

An append-only record of a security-sensitive or governance-relevant action. It records the actor, action, resource, result, request correlation, and redacted metadata.

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
