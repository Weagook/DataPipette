# Contributing to DatePipette

Thank you for your interest in DatePipette. The project is pre-alpha and is establishing its first vertical slice. Focused changes aligned with the approved MVP are preferred over broad feature additions.

## Before proposing a change

1. Read the [product vision](docs/PRODUCT.md) and [MVP scope](docs/MVP.md).
2. Check [open questions](docs/OPEN_QUESTIONS.md) and relevant [ADRs](docs/adr/README.md).
3. Follow the [Git workflow](docs/GIT_WORKFLOW.md) and prepare a local execution plan for non-trivial work.
4. For a feature or architecture change, open a discussion or issue before implementing a large patch.
5. Never include real customer data, credentials, snapshots, or export artifacts.

## Development setup

The repository has not yet reached its runnable monorepo scaffold. Exact setup, test, lint, and migration commands will be added here as the first implementation milestone lands. Documentation-only contributors do not need to install runtime dependencies.

## Change expectations

- Keep pull requests focused on one coherent outcome.
- Add tests that fail before and pass after a behavioral change.
- Update OpenAPI and generated clients together once generation is available.
- Include database migrations for metadata schema changes.
- Explain security, compatibility, and operational consequences.
- Update affected product, architecture, API, or security documentation.
- Do not silently expand MVP scope.

## Architecture decisions

A change that alters a durable system boundary, persistence model, security model, public contract strategy, or major dependency requires an ADR. Use the structure in [docs/adr/README.md](docs/adr/README.md).

## Reporting security issues

Do not open a public issue for a suspected vulnerability. Follow [SECURITY.md](SECURITY.md).

## License of contributions

By intentionally submitting a contribution for inclusion in DatePipette, you agree that it may be distributed under the Apache License 2.0, consistent with the repository license.
