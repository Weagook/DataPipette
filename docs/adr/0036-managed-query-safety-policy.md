# ADR-0036: Managed Query Safety planner policy and audited overrides

- Status: Accepted
- Date: 2026-10-14

## Context

The initial Query Safety Advisor used validated environment settings. That made behavior predictable but required a deployment change to tune thresholds and provided no durable revision that could explain why one validation was blocked. A privileged escape hatch is also necessary for planner false positives, but it must not become an invisible or reusable bypass.

Runtime budgets such as preview timeout, refresh timeout, response bytes, memory, and concurrency are enforced by different executors. Claiming they are dynamically managed before every executor consumes one policy would create a dangerous mismatch between the API and actual enforcement.

## Decision

- Store at most one installation-wide planner policy in metadata PostgreSQL. It contains the existing row, cost, join, sequential-scan, sort, and statistics-age thresholds.
- When no row exists, expose validated installation defaults as revision zero. The first complete replacement with the revision-zero ETag creates revision one; later replacements lock the row and require the current revision.
- Restrict policy reads and replacements to active administrator browser sessions. Replacement requires CSRF, uses an opaque ETag, validates bounds and warning/block ordering, and appends a redacted audit event.
- Resolve the current policy immediately before every live draft validation and persist its revision in the validation JSON artifact. Older artifacts without this field read as revision zero.
- Permit an override only as part of a fresh validation request from an administrator, with a normalized 10–500 character reason, and only if the fresh result is blocked. Downgrade blocking findings to warnings, return `valid_with_warnings`, persist actor/time/reason/codes in the artifact, and append a dedicated audit event.
- Initially exclude dynamic timeout, result-size, memory, and concurrency controls until each applicable execution path can enforce them. [ADR-0037](0037-managed-source-runtime-budgets.md) subsequently adds timeout and result-size controls for source preview and refresh; [ADR-0038](0038-managed-interactive-duckdb-budgets.md) adds interactive DuckDB controls.

## Consequences

- Administrators can tune planner thresholds without a redeploy, and every validation explains which policy revision governed it.
- Optimistic concurrency prevents one administrator from silently overwriting another administrator's policy edit.
- An override unlocks the normal preview/publication path for exactly one validation artifact; editing the draft clears that artifact and therefore clears the override.
- Override reasons are governance metadata visible in the administrator audit API. The API and documentation prohibit secrets or business values in that field.
- Background export budgets, distributed admission control, recommendations, and actual-versus-estimated feedback remain later Phase 2 slices.

## Alternatives considered

- **Continue with environment-only policy:** rejected because it has no administrator workflow or durable revision provenance.
- **Store every runtime limit immediately:** rejected because several long-lived executors currently read immutable process configuration and would not honor updates consistently.
- **Globally disable a blocking rule:** rejected because it weakens every dataset rather than documenting one reviewed exception.
- **Allow analysts to self-override:** rejected because the same principal that authors the query must not unilaterally bypass installation safety policy.
