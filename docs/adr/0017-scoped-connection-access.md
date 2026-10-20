# ADR-0017: Scoped connection access and analyst dataset ownership

- Status: Accepted
- Date: 2026-10-10

## Context

The configured PostgreSQL role is the outer source-security boundary, but it is too broad for day-to-day analyst discovery. Administrators need to expose selected connections, schemas, or relations without sharing credentials or allowing analysts to reconfigure a connection. Analysts must be able to author datasets inside that scope while private drafts remain isolated from other analysts. Revocation must also cover jobs that were queued before access changed.

## Decision

Metadata PostgreSQL stores additive connection grants for either one active analyst or one group. A grant has exactly one exact-name scope:

- `all` exposes every object already visible to the configured PostgreSQL role;
- `schema` exposes every database-visible relation in one schema; and
- `table` exposes one relation and makes its containing schema discoverable.

An active analyst receives the union of direct and current group grants. Administrators bypass connection grants. `Viewer` is a hard role ceiling and cannot use source connections even through a group. There are no deny rules, wildcard identifiers, nested groups, or column-level scopes in this version. Connection configuration and testing remain administrator-only.

Catalog filtering happens in the source queries before pagination. Foreign-key metadata is returned only when both endpoint relations are in effective scope. An inaccessible and an absent connection or catalog object use the same `404` boundary.

An analyst may create a private visual dataset only from an allowed relation and may read, edit, validate, preview, refresh, and inspect refresh state only for datasets they own. Administrators retain unconditional operational access. Dataset ACL `manage` governs sharing; it does not transfer ownership or grant source-authoring rights.

Effective connection access is read from authoritative metadata during create and draft replacement, live validation, preview, refresh request, and again in the worker immediately before source access. Thus a queued refresh fails with the safe code `connection_access_revoked` when its grant or group membership has since been removed. Revocation does not delete definitions or previously published snapshots: snapshot reads continue to use the independent dataset ACL policy.

Grant changes append redacted audit events with identifiers and exact scope names, never credentials, SQL, or business rows.

## Consequences

- Administrators can start narrow and expand access by composing direct and group scopes.
- Analysts can use the backend as a self-service dataset engine without receiving connection-administration privileges.
- Source-touching operations add an authoritative metadata lookup, including worker execution.
- Revoked datasets remain visible to their owner as metadata but cannot be changed or refreshed from that source until access is restored.
- Exact case-sensitive PostgreSQL identifiers avoid ambiguous pattern semantics but require the administrator to grant the names PostgreSQL exposes.
- Existing snapshots remain available after source revocation, so administrators must revoke dataset ACLs separately when historical data must also become unreadable.

## Alternatives considered

- **Rely only on the PostgreSQL role:** rejected because every analyst would discover the same database-visible surface.
- **Copy catalog metadata into the DataPipette database:** deferred because it introduces freshness and invalidation problems before they are needed.
- **Check access only when a dataset is created:** rejected because saved definitions and queued jobs would bypass later revocation.
- **Make connection grants imply dataset access:** rejected because source authoring and consumption of published business data are separate security decisions.
- **Allow dataset `manage` holders to edit definitions:** rejected for now because sharing permission is not ownership transfer.
