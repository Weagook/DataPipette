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
