# ADR-0016: Additive dataset access control with role ceilings

- Status: Accepted
- Date: 2026-10-10

## Context

Datasets are private by default, but analysts need explicit access to detailed rows and exports. Administrators also need groups so the same policy does not have to be repeated for every person. A grant must never turn the installation-wide `Viewer` role into a detailed-data role, and revocation must take effect without waiting for an authorization cache to expire.

## Decision

Metadata PostgreSQL stores named groups, group memberships, and additive dataset grants. A grant targets either one active analyst or one group and contains exactly one permission:

- `view_rows` permits querying detailed snapshot rows;
- `export` permits export lifecycle access, but creating an export also requires `view_rows`; and
- `manage` permits listing, creating, and revoking grants for that dataset.

Effective dataset permissions are evaluated for every protected request. Administrators bypass dataset grants. A dataset owner receives every dataset permission. An active analyst receives the union of direct grants and grants inherited through every group membership. A `Viewer` is always denied detailed dataset rows, exports, and grant management even if a containing group has those grants. There are no deny grants in this version.

Only administrators manage groups and memberships. Dataset owners, administrators, and analysts holding `manage` may manage that dataset's grants. Repeating the same membership or grant is idempotent. APIs conceal an absent dataset and an inaccessible dataset with the same `404` response.

The authorization path reads authoritative metadata rather than using Redis or an in-process permission cache, so membership and grant revocation affects the next request. A previously issued export download capability remains valid until its at-most-five-minute expiry; it is intentionally a short-lived bearer capability and cannot be recalled individually in this version.

Security-relevant changes append redacted audit events containing identifiers and permission names, never business rows, credentials, session material, or capability tokens. Group deletion is deferred until ownership and lifecycle behavior are defined.

## Consequences

- Group membership and grants can be changed independently and combine predictably.
- `manage` is deliberately delegable and therefore should be granted sparingly.
- Every row query and export authorization adds metadata-database work; future caching must preserve prompt revocation or use explicit invalidation.
- A group may contain viewers, but dataset grants to that group remain ineffective for those viewers.
- Connection visibility, chart access, and dashboard-context authorization remain separate future policies.

## Alternatives considered

- **Role-only authorization:** rejected because it cannot express dataset-specific sharing.
- **Deny grants:** deferred because precedence rules and policy explanation would add complexity before a concrete need exists.
- **Redis-cached effective permissions:** rejected for the first version because reliable invalidation is more complex than an authoritative metadata lookup.
- **One broad dataset permission:** rejected because browsing rows, exporting data, and delegating access have different risk profiles.
