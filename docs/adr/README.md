# Architecture decision records

ADRs record durable decisions and their consequences. They explain why the architecture is shaped a certain way; they do not replace implementation plans.

## Status values

- `Proposed`
- `Accepted`
- `Superseded by ADR-NNNN`
- `Rejected`

## Template

```md
# ADR-NNNN: Decision title

- Status: Proposed
- Date: YYYY-MM-DD

## Context

## Decision

## Consequences

## Alternatives considered
```

## Accepted decisions

- [ADR-0001](0001-headless-monorepo-modular-monolith.md): Headless API-first monorepo and modular monolith
- [ADR-0002](0002-single-company-deployment.md): Single-company deployment model
- [ADR-0003](0003-parquet-snapshot-query-model.md): Parquet snapshot query model
- [ADR-0004](0004-safe-dataset-publication.md): Draft and atomic dataset publication
- [ADR-0005](0005-server-side-browser-sessions.md): Server-side opaque browser sessions
- [ADR-0006](0006-local-account-lifecycle.md): Administrator-managed local account lifecycle
- [ADR-0007](0007-encrypted-postgresql-connections.md): Installation-key encryption for PostgreSQL connections
- [ADR-0008](0008-live-postgresql-catalog.md): Live privilege-filtered PostgreSQL catalog discovery
- [ADR-0009](0009-versioned-visual-dataset-definitions.md): Versioned JSONB visual dataset definitions
- [ADR-0010](0010-revision-bound-dataset-validation.md): Revision-bound non-executing dataset validation
- [ADR-0011](0011-bounded-dataset-draft-preview.md): Bounded synchronous dataset draft preview
- [ADR-0012](0012-durable-refresh-run-orchestration.md): Durable idempotent refresh-run orchestration
- [ADR-0013](0013-local-parquet-snapshot-publication.md): Streaming local Parquet snapshot publication
- [ADR-0014](0014-typed-snapshot-row-queries.md): Typed snapshot row queries
- [ADR-0015](0015-snapshot-bound-background-exports.md): Snapshot-bound background exports
- [ADR-0016](0016-additive-dataset-access-control.md): Additive dataset access control with role ceilings
- [ADR-0017](0017-scoped-connection-access.md): Scoped connection access and analyst dataset ownership
- [ADR-0018](0018-versioned-visual-join-trees.md): Versioned visual join trees and advisory foreign-key suggestions
- [ADR-0019](0019-dataset-freshness-projection.md): Dataset freshness and refresh failure projection
- [ADR-0020](0020-durable-refresh-scheduling.md): Durable database-polled refresh scheduling
- [ADR-0021](0021-advanced-read-only-sql-datasets.md): Advanced read-only SQL dataset definitions
- [ADR-0022](0022-shared-s3-compatible-object-storage.md): Shared S3-compatible analytical object storage
- [ADR-0023](0023-analytical-object-lifecycle-cleanup.md): Analytical object lifecycle cleanup
- [ADR-0024](0024-refresh-cancellation-and-bounded-retries.md): Refresh cancellation and bounded execution retries
- [ADR-0025](0025-dataset-version-history-and-schema-impact.md): Dataset version history and revision-bound schema impact
- [ADR-0026](0026-bounded-query-safety-advisor.md): Bounded PostgreSQL Query Safety Advisor
- [ADR-0027](0027-published-dataset-exploration-contract.md): Published dataset exploration contract
- [ADR-0028](0028-private-chart-query-foundation.md): Private chart specifications and snapshot aggregate queries
- [ADR-0029](0029-timezone-aware-chart-time-buckets.md): Timezone-aware chart time buckets
- [ADR-0030](0030-private-dashboard-layouts.md): Private versioned dashboard layouts
- [ADR-0031](0031-independent-dashboard-item-queries.md): Independent dashboard item queries
- [ADR-0032](0032-dashboard-sharing-and-viewer-execution.md): Dashboard sharing and constrained viewer execution
- [ADR-0033](0033-service-account-api-tokens.md): Service accounts and scoped opaque API tokens
- [ADR-0034](0034-generated-typescript-api-client.md): Generated TypeScript API client
- [ADR-0035](0035-administrator-audit-event-api.md): Administrator audit-event read API
- [ADR-0036](0036-managed-query-safety-policy.md): Managed Query Safety planner policy and audited overrides
- [ADR-0037](0037-managed-source-runtime-budgets.md): Managed source preview and refresh runtime budgets
- [ADR-0038](0038-managed-interactive-duckdb-budgets.md): Managed interactive DuckDB runtime budgets
- [ADR-0039](0039-governed-background-analytical-work.md): Managed export budgets and distributed heavy-worker admission
- [ADR-0040](0040-read-only-query-recommendations.md): Read-only query recommendations and refresh execution feedback
