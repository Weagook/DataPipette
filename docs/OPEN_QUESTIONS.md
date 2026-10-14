# Open questions

These questions are intentionally unresolved. They are not permission to invent behavior during implementation; resolve the relevant item before its dependent feature begins.

## Product and UX

- What exact dashboard grid, breakpoints, and mobile behavior should the official frontend use?
- Which chart styling options belong in MVP beyond required data configuration?
- Should temporary table filters be restorable from URL state without becoming saved dataset configuration?
- What is the ownership-transfer experience when a user is disabled or deleted?
- Should analytical resources be archived, soft-deleted, or hard-deleted, and how are dependencies handled?

## Dataset engine

- Which non-aggregate row-level operations belong in the visual builder beyond projection and joins?
- Are calculated row-level fields part of MVP or a later milestone?
- Which PostgreSQL types require special normalization in Parquet and API JSON?
- How should identifiers behave when source tables or columns are renamed?

## Execution and storage

- Benchmark whole-object S3 materialization against direct/ranged DuckDB reads and a verified local cache under the reference workload.
- Define Parquet file sizing, row-group sizing, compression, and object-key layout from benchmarks.
- Decide whether a dead-letter workflow is needed beyond durable terminal refresh state and administrator-visible safe errors.

## API

- Approve additional API-token scopes beyond the implemented `dashboards:read` scope.
- Approve endpoint names and action conventions before publishing OpenAPI v1.
- Define cursor encoding and invalidation behavior precisely.
- Define rate-limit policy for humans, service accounts, queries, refresh, and export.

## Security and operations

- Define installation-key storage, backup, and rotation.
- Define account recovery beyond the implemented first-administrator bootstrap.
- Define metadata PostgreSQL backup and disaster-recovery objectives.
- Decide whether production adds customer-managed KMS keys and workload-identity/STS credential providers beyond the implemented optional `AES256` request and static secret settings.
- Publish the private vulnerability-reporting contact before the first release.

## Project

- Select the frontend design system after the first workflow wireframes.
- Decide package-manager and workspace conventions for the frontend scaffold.
- Establish release versioning and changelog automation before the first tagged release.
