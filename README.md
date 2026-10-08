# DatePipette

![DatePipette — precise data transformed into analytics](docs/assets/datepipette-hero-minimal.svg)

DatePipette is an API-first, snapshot-based business intelligence platform for PostgreSQL. It is designed to work both as a standalone analytics application and as a headless BI backend embedded into other products.

> **Project status:** pre-alpha. The product contract and architecture are documented, and the first backend foundation is under active development.

## Why DatePipette?

DatePipette aims to make a common analytics workflow simple and safe:

1. Connect a read-only PostgreSQL database.
2. Build a virtual dataset with a no-code/low-code query builder or one advanced read-only SQL statement.
3. Refresh the dataset into an immutable Parquet snapshot.
4. Browse and filter detailed rows without repeatedly querying the source database.
5. Build charts and dashboards from the published snapshot.
6. Use the same capabilities through a stable public API.

The backend is the product core. The official web application is a client of the same API available to external integrations.

## Planned MVP

- Multiple PostgreSQL connections in a single-company installation
- Visual datasets with `INNER JOIN` and `LEFT JOIN`
- Foreign-key relationship suggestions and manually configured joins
- Advanced read-only SQL datasets
- Draft, validation, preview, refresh, and atomic publication
- Parquet snapshots in local development storage or S3-compatible storage
- Scheduled and manual refresh through Celery
- Tabular browsing, typed filters, pagination, CSV export, and XLSX export
- Bar, line, pie/donut, and KPI visualizations
- Dashboards containing charts from multiple datasets
- Local accounts, groups, roles, object ACLs, service accounts, and scoped API tokens
- Append-only application audit log

See [MVP scope](docs/MVP.md) for explicit boundaries and acceptance criteria.

## Architecture at a glance

```text
React web app / External clients
               |
          Public REST API
               |
        FastAPI application
          /            \
Metadata PostgreSQL   Redis / Celery
                           |
                 PostgreSQL sources
                           |
                    Parquet snapshots
                           |
                 Local storage or S3

Interactive reads: Parquet snapshot -> DuckDB -> API
```

The API and workers are designed to scale horizontally. Production state lives in PostgreSQL, Redis, and S3-compatible storage rather than an API container's local filesystem.

## Documentation

Start with the [documentation index](docs/README.md).

- [Product vision](docs/PRODUCT.md)
- [MVP scope](docs/MVP.md)
- [Requirements](docs/REQUIREMENTS.md)
- [Domain model](docs/DOMAIN_MODEL.md)
- [Architecture](docs/ARCHITECTURE.md)
- [API guidelines](docs/API_GUIDELINES.md)
- [Security model](docs/SECURITY.md)
- [Roadmap](docs/ROADMAP.md)
- [Architecture decisions](docs/adr/README.md)

## Repository direction

DatePipette is a uv workspace. The independently packaged backend lives in `backend/`; local PostgreSQL and Redis infrastructure lives in `deploy/compose/`. The frontend will be added as an independently buildable application when the first UI vertical slice begins.

To run the current backend foundation:

```bash
uv sync --locked --all-packages --all-groups
docker compose -f deploy/compose/compose.yaml up -d --wait
uv run --package datepipette alembic -c backend/alembic.ini upgrade head
uv run --package datepipette datepipette
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for configuration, verification, and cleanup commands.

## Contributing

The project is not yet accepting broad feature work while its first vertical slice is being established. Design discussions and focused improvements are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change.

## Security

Do not report vulnerabilities in a public issue. Follow [SECURITY.md](SECURITY.md).

## License

DatePipette is licensed under the Apache License 2.0.
