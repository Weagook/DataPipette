# ADR-0002: Single-company deployment model

- Status: Accepted
- Date: 2026-10-08

## Context

An earlier concept considered multiple independent organizations inside one installation. That model requires tenant-aware identity, metadata, query filtering, operational isolation, and extensive cross-tenant security verification.

## Decision

One DataPipette installation serves one company. The installation may contain many users, groups, service accounts, PostgreSQL connections, datasets, charts, and dashboards, but it has no tenant or organization boundary in MVP.

## Consequences

- Authorization focuses on roles, groups, ACLs, and connection visibility rather than tenant filtering.
- Connected databases are treated as company-owned sources.
- The metadata model must not pretend to provide SaaS tenant isolation.
- A future multi-tenant product requires a new ADR and threat model rather than simply adding `organization_id` ad hoc.

## Alternatives considered

A shared SaaS installation with logical organization isolation was rejected to keep the first product understandable and secure.
