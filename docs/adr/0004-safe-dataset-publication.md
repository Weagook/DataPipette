# ADR-0004: Draft and atomic dataset publication

- Status: Accepted
- Date: 2026-10-08

## Context

Editing joins, columns, or SQL can produce an invalid query or remove columns used by charts. Replacing a working dataset definition before its new result is known to be valid would break readers and dashboards.

## Decision

A dataset has a published definition and an independently editable draft. Draft publication performs validation, dependency impact analysis, a complete refresh, stored-output validation, and an atomic metadata switch to the new definition and snapshot.

Breaking chart dependencies block publication by default. A user may explicitly force publication, in which case affected charts are retained in an `invalid` state with a structured reason.

## Consequences

- Readers continue using the last successful version during editing and refresh.
- Publication is a background workflow rather than a simple metadata update.
- Draft and published schemas must be distinguishable in APIs and storage.
- Impact analysis becomes a required domain capability.
- Failed publication leaves recoverable draft state and does not require snapshot rollback.

## Alternatives considered

Immediate in-place mutation was rejected because it exposes users to partial, invalid, or unrefreshed definitions.
