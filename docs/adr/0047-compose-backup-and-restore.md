# ADR-0047: Operator backup and restore for the Compose installation

- Status: Accepted
- Date: 2026-10-29

## Context

The evaluation topology keeps every durable product state in three places: metadata PostgreSQL (identities, sessions, grants, encrypted connections, datasets, charts, dashboards, schedules, run history, and audit events), the private S3-compatible bucket (published Parquet snapshots and export files), and the operator's `.env` file, which holds `DATAPIPETTE_CONNECTION_ENCRYPTION_KEY`. A lost disk, an accidental `docker compose down --volumes`, or a failed upgrade previously had no documented recovery path, and losing the key alone made stored source credentials unrecoverable.

Metadata rows reference snapshot objects by key, size, and SHA-256. A metadata dump without the matching objects restores dashboards that cannot read their data until every dataset is refreshed again, which may be impossible when a source is unavailable.

## Decision

- Backup and restore are operator commands run on the Compose host, not a product API or UI capability.
- `deploy/compose/backup.sh create <root> [--keep N]` writes one timestamped backup set: a custom-format `pg_dump` of metadata PostgreSQL, a copy of every bucket object with a SHA-256 manifest, a `MANIFEST` (format, application revision, Alembic revision, key fingerprint), and `SHA256SUMS`. The set is assembled under a `.partial` name and renamed only when complete; `--keep` removes the oldest complete sets.
- The metadata dump is taken before the object copy. Active snapshots are never deleted and superseded snapshots keep a cleanup grace period, so every object the dump references is still present; objects created afterwards become orphans that the existing lifecycle cleanup removes.
- The encryption key is never written to a backup set. The manifest stores only a domain-separated SHA-256 fingerprint of the decoded key. Restore refuses to start when the configured key has a different fingerprint, and finishes by decrypting every stored connection secret with the configured key.
- `restore <set> --yes` verifies checksums, objects, and the dump; stops the application services; recreates the metadata database from the dump; flushes Redis, which only holds the Celery transport and login throttling; replaces the bucket contents, deleting objects absent from the backup; and starts the stack, whose migration service upgrades an older schema.
- Object copy, verification, import, key fingerprinting, and key verification are implemented in the backend package as `datapipette-backup` and run through the existing backend image, so the host still needs only Docker and Compose.
- The documented recovery objectives for the evaluation topology are a daily scheduled backup (RPO 24 hours) and a restore into an existing or freshly started stack within one hour.

## Consequences

- An installation can be rebuilt on a new host from a backup set, the repository, and a separately stored `.env`.
- Backup sets contain business data and password hashes; they are created with owner-only permissions and must be stored and transferred like the database itself.
- Source databases are outside the backup boundary. A restored connection decrypts correctly but succeeds only when its source is reachable again.
- Restore is destructive and requires an explicit `--yes`. Browser sessions active at backup time are restored; sessions created later are lost, as is queued Celery work, which the durable run recovery re-dispatches.
- Restoring a set made by a newer application revision than the checked-out code is unsupported, because migrations only upgrade.
- Off-host replication, encryption at rest of backup sets, key rotation, and managed-database point-in-time recovery remain production concerns.

## Alternatives considered

- **Copying Docker volumes with `tar`:** rejected because a consistent PostgreSQL copy requires stopping the database, and raw MinIO volume layouts are tied to its internal format and version.
- **Storing the key in a password-encrypted archive:** rejected because the archive and the key would then be lost or leaked together, and it adds a second secret to manage.
- **Metadata-only backups that rely on re-running every refresh:** rejected because sources may be unavailable or changed, so dashboards would not return to their backed-up state.
- **A backup endpoint in the API:** rejected because it would require the API to hold host-level storage access and would turn an operational procedure into a privileged network surface.
