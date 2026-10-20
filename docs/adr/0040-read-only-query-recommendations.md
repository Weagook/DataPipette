# ADR-0040: Read-only query recommendations and refresh execution feedback

- Status: Accepted
- Date: 2026-10-15

## Context

Query Safety findings explain planner risk, but a client still has to translate each finding into a useful next action. PostgreSQL estimates can also differ materially from real results, especially when statistics are stale. Running `EXPLAIN ANALYZE` during validation would obtain actual execution data, but it would execute the query before the user requests a preview or refresh and would weaken the non-executing validation boundary.

Index advice is particularly easy to overstate. A sequential scan can be PostgreSQL's correct choice, and DataPipette neither owns the source schema nor has enough workload history to prove that an index will help.

## Decision

- Derive typed recommendation codes from the immutable validation findings. The API returns identifiers and evidence for client rendering instead of localized prose.
- For visual datasets, associate a large sequential scan with explicit join conditions and expose those source column names as index-review candidates. For advanced SQL, return relation-level advice only because reliable expression and column lineage is not available.
- Never return executable DDL and never execute `CREATE INDEX`, `ANALYZE`, query rewrites, or any other source mutation. A database administrator must review each recommendation against the wider workload and operational constraints.
- Preserve recommendations inside the revision-bound validation JSON. Older artifacts without the field deserialize to an empty recommendation list.
- Capture the validation-time root planner row estimate when a refresh attempt is claimed. Measure the already-authorized executor duration and record the candidate snapshot's actual row count when available.
- Expose actual-to-estimated row ratio only when both values exist and the estimate is positive. Do not issue an extra count query and do not enable `EXPLAIN ANALYZE`.
- Metrics describe the latest completed attempt, including while a retry is queued. The next claim clears prior attempt metrics; planner estimate provenance remains the validation artifact used by that attempt.

## Consequences

- The official frontend and external clients can present consistent actionable advice without parsing finding text.
- Index candidates are intentionally phrased as review prompts, not guaranteed optimizations. They may be absent even when an expert could devise a useful expression or partial index.
- Successful refreshes provide direct estimate-versus-actual feedback, while failures can still expose duration and a candidate row count when materialization reached that point.
- No new query or source privilege is required, and DataPipette's source access remains read-only.
- Historical aggregation, automatic tuning, plan capture, and benchmark baselines remain separate work.

## Alternatives considered

- **Run `EXPLAIN ANALYZE` during validation:** rejected because it executes potentially heavy source work before explicit refresh and duplicates the query.
- **Generate `CREATE INDEX` statements:** rejected because syntactically valid DDL can still be operationally harmful and may imply an unsupported guarantee.
- **Parse free-form planner expressions for SQL column candidates:** rejected because aliases, expressions, functions, and nested queries make partial parsing misleading; only structured visual join metadata is used.
- **Compare estimates with a separate `COUNT(*)`:** rejected because it doubles source work and can observe a different database state from materialization.
