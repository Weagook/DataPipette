# ADR-0006: Administrator-managed local account lifecycle

- Status: Accepted
- Date: 2026-10-08

## Context

The first dataset workflow requires an administrator to provision analyst and viewer accounts before groups, invitations, email delivery, SSO, or resource ownership transfer exist. Account management must not make the installation inaccessible, leave disabled users with working sessions, or introduce physical deletion before ownership rules are defined.

## Decision

- Administrators create active local accounts with an email, display name, initial password, and one system role.
- Account email is the immutable normalized login identifier in the MVP. Email reassignment requires a future explicit workflow.
- Accounts may be disabled and later reactivated; they are not physically deleted.
- Disabling an account atomically revokes all of its browser sessions.
- Administrators may explicitly revoke all sessions for any account without disabling it.
- An administrative mutation may not disable or demote the last active administrator. The invariant is serialized in metadata PostgreSQL.
- User collections use bounded cursor pagination ordered by creation time and identifier.
- Creation, profile/role/status changes, and session revocation create redacted audit events.

## Consequences

- The installation always retains at least one active administrator through supported APIs.
- Ownership-transfer behavior can be designed when owned analytical resources exist, without recovering deleted identity rows.
- Initial passwords must be delivered out of band until invitation and recovery workflows are implemented.
- Reactivating an account does not restore revoked sessions; the user signs in again.
- Administrators can demote or disable themselves only when another active administrator remains.

## Alternatives considered

- Physical deletion was rejected because future datasets, charts, dashboards, exports, and audit events need stable actor and owner identities.
- Mutable login email was deferred because reassignment needs explicit uniqueness, verification, audit, and integration semantics.
- Allowing the final administrator to be disabled was rejected because recovery would require direct database intervention.
- Automatically generated or emailed passwords were rejected until notification delivery and invitation-token security are designed.
