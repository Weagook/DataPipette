# Evaluation deployment

DataPipette ships a self-contained Docker Compose topology for evaluation on one machine. The host needs only Git, Docker Engine, and Docker Compose v2; Python, uv, Node.js, PostgreSQL, Redis, and MinIO are contained in the deployment.

This topology is not yet the production reference architecture. It uses an embedded MinIO-compatible object store and an environment file as the source for local secrets, and it does not provision DNS, TLS certificates, off-host backup storage, or external observability. Local backup and restore commands are described in [Backup and restore](#backup-and-restore).

## Upgrade from the former DatePipette name

The pre-alpha product was renamed to DataPipette. New installations use the `DATAPIPETTE_` environment prefix, `datapipette` package and command names, and `datapipette_*` Compose volumes. An existing installation must retain its current database names, credentials, encryption key, S3 bucket, and physical volume names during the upgrade.

Before pulling the rename, back up `.env` and the three persistent volumes. After pulling it, rename only the environment-variable keys from `DATEPIPETTE_` to `DATAPIPETTE_`; do not replace their values mechanically. Add these compatibility bindings for the volumes created by the former Compose project:

```dotenv
DATAPIPETTE_METADATA_VOLUME_NAME=datepipette_metadata-data
DATAPIPETTE_REDIS_VOLUME_NAME=datepipette_redis-data
DATAPIPETTE_MINIO_VOLUME_NAME=datepipette_minio-data
```

Stop the former project without deleting its volumes, then start DataPipette normally:

```bash
docker compose -p datepipette --env-file .env -f deploy/compose/compose.yaml down
docker compose --env-file .env -f deploy/compose/compose.yaml up -d --build --wait
```

Stored PostgreSQL credentials remain decryptable through the versioned legacy envelope reader. Existing browser sessions, snapshot cursors, and short-lived export links are intentionally not migrated; users sign in again and can reissue those transient values. Metadata, datasets, snapshots, exports, users, grants, and saved source connections remain in place.

## First start

Clone the private repository, copy the tracked template, restrict it to the current operating-system user, and replace every development credential:

```bash
git clone git@github.com:Weagook/DataPipette.git
cd DataPipette
cp deploy/compose/.env.example .env
chmod 600 .env
```

At minimum, set unique values for:

- `DATAPIPETTE_POSTGRES_PASSWORD`
- `DATAPIPETTE_S3_ACCESS_KEY_ID`
- `DATAPIPETTE_S3_SECRET_ACCESS_KEY`
- `DATAPIPETTE_CONNECTION_ENCRYPTION_KEY`
- `DATAPIPETTE_BOOTSTRAP_ADMIN_EMAIL`
- `DATAPIPETTE_BOOTSTRAP_ADMIN_DISPLAY_NAME`
- `DATAPIPETTE_BOOTSTRAP_ADMIN_PASSWORD`

Generate ordinary random secrets with `openssl rand -hex 32`. Generate the connection-encryption key as URL-safe base64 without padding from exactly 32 random bytes. For example, on a machine with Python:

```bash
python3 -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("="))'
```

The encryption key must be backed up separately from metadata PostgreSQL. Losing it makes saved source-connection credentials unrecoverable.

Start or converge the complete installation with one command:

```bash
docker compose --env-file .env -f deploy/compose/compose.yaml up -d --build --wait
```

The dependency graph performs the following work:

1. starts metadata PostgreSQL, Redis, and private MinIO storage;
2. builds one non-root backend image and one frontend image;
3. applies all Alembic migrations and initializes the private object bucket;
4. creates the first administrator only when no administrator exists;
5. starts the API, refresh/export worker, and durable scheduler;
6. exposes the React SPA and same-origin `/api` through Nginx.

Compose mounts the mode-`0600` `.env` file read-only into the one-shot bootstrap container as a secret source. Because Compose implements file-backed secrets as bind mounts, only that short-lived container runs as the container root user so it can read the root-owned file; the API, worker, and scheduler continue to run as UID `10001`. The administration CLI reads only `DATAPIPETTE_BOOTSTRAP_ADMIN_PASSWORD`, and the password is not included in the process command or ordinary container environment. Bootstrap is deliberately convergent rather than reconciling: every later `up` skips creation when an administrator exists and never changes that account's email, name, or password.

By default, the application listens only on `http://127.0.0.1:8080`. Use an SSH tunnel for a remote evaluation:

```bash
ssh -L 8080:127.0.0.1:8080 user@server
```

Then open `http://127.0.0.1:8080` locally. To bind the evaluation gateway to every server interface, explicitly set `DATAPIPETTE_HTTP_BIND_ADDRESS=0.0.0.0`; do this only behind an appropriate firewall and TLS reverse proxy. PostgreSQL, Redis, MinIO API, and the MinIO console remain loopback-only.

The bundled stack sets `DATAPIPETTE_S3_DOWNLOAD_DELIVERY=proxy`: CSV/XLSX downloads stay on the public application origin while the API retrieves and verifies the object from private MinIO, streams the attachment, and deletes its temporary copy. A deployment with browser-reachable HTTPS S3 should use the default `redirect` mode to avoid proxying large files through the API.

## Operations

Inspect state and health:

```bash
docker compose --env-file .env -f deploy/compose/compose.yaml ps
docker compose --env-file .env -f deploy/compose/compose.yaml logs -f api worker scheduler frontend
```

The frontend container has a gateway-local `GET /healthz` endpoint. Its Docker health check proves that Nginx can serve traffic without conflating gateway liveness with API readiness; the API container independently checks PostgreSQL and Redis. After `up --wait`, verify the public path and same-origin proxy:

```bash
curl --fail --silent --show-error --output /dev/null http://127.0.0.1:8080/healthz
curl --fail --silent --show-error --head http://127.0.0.1:8080/login
curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/health/ready
```

HTML and SPA fallback responses are `no-store`. Fingerprinted `/assets/` responses are cached for one year as immutable. The gateway adds a restrictive same-origin Content Security Policy, clickjacking protection, MIME sniffing protection, a strict-origin referrer policy, and disables camera, geolocation, and microphone browser capabilities. TLS and HSTS remain the responsibility of the production ingress because this evaluation gateway intentionally serves plain loopback HTTP.

Rebuild after pulling a new revision; migrations and bootstrap remain idempotent:

```bash
git pull --ff-only
docker compose --env-file .env -f deploy/compose/compose.yaml up -d --build --wait
```

Stop containers while retaining all named volumes:

```bash
docker compose --env-file .env -f deploy/compose/compose.yaml stop
```

Remove containers and the private network while retaining data:

```bash
docker compose --env-file .env -f deploy/compose/compose.yaml down
```

Do not add `--volumes` unless permanent deletion of metadata, object storage, and queued state is intended and independently backed up.

## Backup and restore

`deploy/compose/backup.sh` backs up and restores the running installation. It uses the backend image already built by Compose, so the host still needs only Docker. The target objectives for this topology are one backup per day (at most 24 hours of lost changes) and a restore within one hour. The design is recorded in [ADR-0047](adr/0047-compose-backup-and-restore.md).

A backup set is one directory, `datapipette-<UTC timestamp>`, containing:

- `metadata.dump` — a PostgreSQL custom-format dump of all DataPipette metadata;
- `objects/` — every snapshot and export object with a SHA-256 manifest;
- `MANIFEST` — format, application revision, schema revision, and the encryption-key fingerprint;
- `SHA256SUMS` — checksums of the files above.

The connection-encryption key and `.env` are never included. Store a copy of `.env`, or at least `DATAPIPETTE_CONNECTION_ENCRYPTION_KEY`, in a password manager or another location separate from the backups; without it, restored source passwords cannot be decrypted.

Create a backup and keep the seven most recent sets:

```bash
deploy/compose/backup.sh create /var/backups/datapipette --keep 7
```

Run it daily from the repository directory with cron, for example at 03:00 server time:

```cron
0 3 * * * cd /opt/DataPipette && deploy/compose/backup.sh create /var/backups/datapipette --keep 7 >>/var/log/datapipette-backup.log 2>&1
```

Backups on the same disk do not survive the loss of that disk. Copy each new set to another host or object store, for example with `rsync` or `rclone`, and protect that copy like the production database: it contains password hashes, encrypted source credentials, audit history, and business data.

Check a set at any time without changing the installation:

```bash
deploy/compose/backup.sh verify /var/backups/datapipette/datapipette-20261029T030000Z
```

Restore replaces all metadata, objects, browser sessions, and queued work. On a new host, clone the repository at the same or a newer revision, put the original `.env` in place, start the stack once with `docker compose --env-file .env -f deploy/compose/compose.yaml up -d --build --wait`, then run:

```bash
deploy/compose/backup.sh restore /var/backups/datapipette/datapipette-20261029T030000Z --yes
```

The command verifies every checksum, refuses to continue when the configured encryption key differs from the one recorded in the backup, stops the application services, recreates the metadata database, clears Redis, replaces the bucket contents, starts the stack, and finally decrypts every stored connection secret to prove the key is correct. Source databases are not part of the backup: a restored connection works once its source is reachable again. Set `DATAPIPETTE_ENV_FILE` when the environment file is not the repository's `.env`.

## Source PostgreSQL connectivity

Both `api` and `worker` must be able to resolve and connect to every configured business PostgreSQL host. Use a stable DNS name or network address and a dedicated read-only database role. When PostgreSQL runs directly on the same Linux host as Docker, use `host.docker.internal`; Compose maps it to the host gateway for the two source-touching services. Do not use `localhost`, because inside a container it refers to that container itself.

Prefer `verify-full` with an explicit CA certificate. Source credentials are encrypted with `DATAPIPETTE_CONNECTION_ENCRYPTION_KEY`, but network reachability and PostgreSQL privileges remain deployment responsibilities.

## Production boundary

Before an internet-facing production rollout, replace or extend this evaluation topology with:

- TLS and an owned domain at the ingress;
- managed or independently backed-up PostgreSQL, Redis, and S3-compatible storage;
- a production secret manager or Docker/Kubernetes secret delivery;
- immutable published images rather than server-side source builds;
- off-host backup replication, regular restore drills, metrics, logs, alerts, and capacity benchmarks;
- `DATAPIPETTE_ENVIRONMENT=production` with an HTTPS S3 endpoint.

Production mode intentionally rejects the built-in development encryption key, local object storage, and non-HTTPS custom S3 endpoints.
