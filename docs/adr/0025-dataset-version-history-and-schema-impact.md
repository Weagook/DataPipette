# ADR-0025: Dataset version history and revision-bound schema impact

- Status: Accepted
- Date: 2026-10-11

## Context

DataPipette already publishes a validated draft as an immutable dataset-version row and keeps older versions as superseded metadata, but clients can read only the mutable draft. Before publication, an analyst needs to understand how the current validated output differs from the active published contract. This must detect both intentional editor changes and source drift without executing the business query or weakening the last-known-good snapshot.

Charts do not exist yet, so inventing dependency records would create a false implementation. The comparison contract should nevertheless be ready to attach real chart impacts later.

## Decision

- Expose owner/administrator reads for immutable `published` and `superseded` versions. History is bounded, cursor-paginated newest first, and returns the complete stored definition plus its revision-bound validation artifact. Draft versions remain available only through the existing draft endpoint and cannot be read through historical-version identifiers.
- Compute impact on demand from authoritative metadata; do not persist a second derived report. The operation requires CSRF, the current draft ETag, current source-relation authorization, and a validation artifact bound to that exact revision.
- Compare output fields by stable UUID. Report added, removed, renamed, PostgreSQL type, nullability, unavailable-result, display, and visual-source changes with before/after field contracts.
- Classify removal, rename, type change, newly nullable output, and a defined field absent from the validated result as breaking. Classify addition, newly required output, display-only change, and visual source remapping that preserves the result contract as compatible.
- Before first publication, use no baseline and report validated fields as compatible additions. An invalid current validation may still produce a useful report, but it is not ready for publication.
- Return empty affected/blocking dependency collections until chart resources are implemented. Breaking schema changes are visible now; blocking and force-publication semantics from ADR-0004 become enforceable only when real dependent resources exist.
- Record a redacted audit event with revision, validation state, counts, and stable change kinds. Do not audit definitions, generated SQL, source identifiers, field names, or raw errors.

## Consequences

- The future UI can show exact immutable history and explain a proposed schema change before starting a refresh.
- Source drift appears after explicit live validation: a still-defined field missing from the result is reported as unavailable and breaking.
- Stable field IDs make comparison deterministic across renames and display changes.
- The report is a point-in-time view protected by the draft ETag; a concurrent edit requires revalidation and recomputation.
- Version history exposes generated SQL only to the same owner/administrator boundary already allowed to read the draft.

## Alternatives considered

- **Persist every impact report:** rejected because it duplicates version/validation authority and creates invalidation work without chart dependencies.
- **Compare fields by name:** rejected because rename detection and no-code editor identity require stable UUIDs.
- **Query PostgreSQL during impact analysis:** rejected because live validation already owns bounded source inspection and stores the exact revision-bound result schema.
- **Expose mutable drafts in version history:** rejected because it blurs immutable history and duplicates the optimistic-concurrency draft contract.
