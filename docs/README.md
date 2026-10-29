# DataPipette documentation

This directory is the source of truth for DataPipette's product and technical design. Documents describe approved direction, not necessarily functionality already implemented. Implementation status belongs in the roadmap and issues.

## Read in this order

1. [PRODUCT.md](PRODUCT.md) — the problem, audience, principles, and non-goals.
2. [MVP.md](MVP.md) — the first releasable product boundary.
3. [REQUIREMENTS.md](REQUIREMENTS.md) — functional and non-functional requirements.
4. [DOMAIN_MODEL.md](DOMAIN_MODEL.md) — the language and lifecycle of core objects.
5. [ARCHITECTURE.md](ARCHITECTURE.md) — components, data flows, and scaling model.
6. [FRONTEND.md](FRONTEND.md) — official UI principles, information architecture, and delivery slices.
7. [API_GUIDELINES.md](API_GUIDELINES.md) — rules for the public API.
8. [SECURITY.md](SECURITY.md) — trust boundaries, authentication, authorization, and data protection.
9. [DEPLOYMENT.md](DEPLOYMENT.md) — one-command evaluation topology, single-server production overlay, operations, and backups.
10. [ROADMAP.md](ROADMAP.md) — incremental delivery sequence.
11. [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) — decisions intentionally left unresolved.
12. [GIT_WORKFLOW.md](GIT_WORKFLOW.md) — branch, commit, review, and local planning conventions.
13. [adr/](adr/README.md) — durable architecture decisions and their consequences.
14. [openapi.json](openapi.json) — generated, reviewable API contract verified in CI.
15. [TypeScript API client](../clients/typescript/README.md) — generated client, verification commands, and integration example.

## Documentation rules

- Prefer one authoritative location for each decision and link to it elsewhere.
- Mark planned behavior as planned; never present it as implemented.
- Record durable architectural changes in an ADR.
- Keep API examples consistent with the generated OpenAPI contract once implementation starts.
- Update affected documentation in the same pull request as a behavior change.
- Do not commit private discovery notes or credentials.
