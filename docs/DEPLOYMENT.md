# Deployment

DataPipette ships a self-contained Docker Compose topology for one machine. Used alone, `compose.yaml` is an evaluation installation that listens on plain HTTP; adding `compose.production.yaml` turns the same topology into a single-server production installation with HTTPS (see [Production on one server](#production-on-one-server)). The host needs only Git, Docker Engine, and Docker Compose v2; Python, uv, Node.js, PostgreSQL, Redis, and MinIO are contained in the deployment.

The evaluation topology alone is not meant for real users. It uses an embedded MinIO-compatible object store and an environment file as the source for local secrets, and it does not provision DNS, TLS certificates, off-host backup storage, or external observability. Local backup and restore commands are described in [Backup and restore](#backup-and-restore).

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

Then open `http://127.0.0.1:8080` locally. To bind the evaluation gateway to every server interface, explicitly set `DATAPIPETTE_HTTP_BIND_ADDRESS=0.0.0.0`; do this only behind an appropriate firewall and TLS reverse proxy, or use the [production overlay](#production-on-one-server) instead. The gateway and API trust `X-Forwarded-For` only from private network addresses, so sign-in throttling counts each browser separately behind such a proxy. PostgreSQL, Redis, MinIO API, and the MinIO console remain loopback-only.

Interactive queries and exports read snapshots through a verified local cache on the `snapshot-cache` volume, shared by the API and worker. `DATAPIPETTE_SNAPSHOT_CACHE_MAX_MB` limits it (8,192 MB by default in Compose; `0` disables it); keep that much free disk space on the host. The cache is regenerable and is not part of backups.

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

## Production on one server

`deploy/compose/compose.production.yaml` is an overlay for one Linux server that people reach over the internet or an office network. The design is recorded in [ADR-0053](adr/0053-single-server-production-compose.md). On top of `compose.yaml` it:

- adds a Caddy gateway that obtains and renews a TLS certificate for your domain automatically, redirects HTTP to HTTPS, and sends HSTS;
- publishes only ports 80 and 443; PostgreSQL, Redis, MinIO, the Nginx gateway, and the API have no host ports at all;
- runs the API, worker, and scheduler with `DATAPIPETTE_ENVIRONMENT=production`, which forces secure session cookies, disables `/docs`, and rejects the development encryption key;
- refuses to start when the domain, database password, object-storage credentials, or encryption key are missing.

Embedded MinIO is reached over plain HTTP inside the private Compose network, which never leaves the host; `DATAPIPETTE_S3_PRIVATE_NETWORK_HTTP=true` allows exactly that, and any other production S3 endpoint must still use HTTPS.

### Requirements

- a Linux server with Docker Engine and Docker Compose v2.24 or newer;
- a domain name whose DNS `A` (and, if used, `AAAA`) record points to the server;
- inbound TCP 80 and 443 (and UDP 443 for HTTP/3) open in any firewall; port 80 is needed for certificate issuance and renewal.

### Install

```bash
git clone git@github.com:Weagook/DataPipette.git /opt/DataPipette
cd /opt/DataPipette
deploy/compose/new-env.sh --domain bi.example.com --admin-email you@example.com --admin-name "Your Name" --timezone Europe/Moscow
```

To run a published release instead of building from source, add `--release` with its version, for example `--release 0.1.0` (see [Releases and upgrades](#releases-and-upgrades)).

The script writes `.env` with mode `600` and unique random values for the database password, object-storage credentials, connection-encryption key, and first administrator password. It prints no secret and never overwrites an existing file. Copy `.env` to a password manager now: the encryption key cannot be recovered, and backups deliberately do not contain it.

Start the installation:

```bash
docker compose --env-file .env -f deploy/compose/compose.yaml -f deploy/compose/compose.production.yaml up -d --build --wait
```

Open `https://bi.example.com` and sign in as the administrator with `DATAPIPETTE_BOOTSTRAP_ADMIN_PASSWORD` from `.env`. The first certificate is usually issued within a minute; `docker compose ... logs gateway` shows progress and any DNS or firewall problem.

Use the same two `-f` options for every later `up`, `ps`, `logs`, `stop`, and `down`.

### Daily backup

Schedule the backup from [Backup and restore](#backup-and-restore) with the production Compose files. As root, create `/etc/cron.d/datapipette-backup` to run it every day at 03:00 server time and keep the seven most recent sets:

```bash
cat >/etc/cron.d/datapipette-backup <<'EOF'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 3 * * * root cd /opt/DataPipette && DATAPIPETTE_COMPOSE_FILES=deploy/compose/compose.yaml:deploy/compose/compose.production.yaml deploy/compose/backup.sh create /var/backups/datapipette --keep 7 >>/var/log/datapipette-backup.log 2>&1
EOF
```

Run the same command once by hand to confirm it works, then check `/var/log/datapipette-backup.log` the next morning.

Copy the backups to another machine as well; a backup on the same disk does not survive the loss of that disk.

### Releases and upgrades

Each [release](https://github.com/Weagook/DataPipette/releases) publishes ready images for `linux/amd64` and `linux/arm64`: `ghcr.io/weagook/datapipette-backend` and `ghcr.io/weagook/datapipette-frontend`. They are tagged with the exact version (`0.1.0`), the minor line (`0.1`), and `latest`. An installation uses them when `.env` contains:

```bash
DATAPIPETTE_BACKEND_IMAGE=ghcr.io/weagook/datapipette-backend
DATAPIPETTE_FRONTEND_IMAGE=ghcr.io/weagook/datapipette-frontend
DATAPIPETTE_IMAGE_TAG=0.1.0
```

Without these lines the images are built from the checked-out source, as before. Pin an exact version in production, so that an upgrade happens only when you decide.

To upgrade:

1. Read the [changelog](../CHANGELOG.md) for every version between yours and the new one, including any manual steps.
2. Create a backup: `DATAPIPETTE_COMPOSE_FILES=deploy/compose/compose.yaml:deploy/compose/compose.production.yaml deploy/compose/backup.sh create /var/backups/datapipette`.
3. Check out the new tag, so that Compose files and scripts match the images (`git fetch --tags && git checkout vX.Y.Z`), and set `DATAPIPETTE_IMAGE_TAG=X.Y.Z` in `.env`.
4. Pull and start. Migrations run automatically before the application starts:

   ```bash
   docker compose --env-file .env -f deploy/compose/compose.yaml -f deploy/compose/compose.production.yaml pull
   docker compose --env-file .env -f deploy/compose/compose.yaml -f deploy/compose/compose.production.yaml up -d --no-build --wait
   ```

Database migrations only move forward. To go back to an older version, restore the backup from step 2 with the older tag checked out and `DATAPIPETTE_IMAGE_TAG` set back.

### Capacity

On 4 CPUs and 8 GB with three API processes, about 80 people can work with small and medium dashboards at the same time, with views opening in about half a second ([measurement](../benchmarks/CAPACITY_2026-10-30.md)). `DATAPIPETTE_API_WORKERS` sets the number of API processes. `new-env.sh` sets it to one per CPU minus one, up to four. Each process uses about 150 MB of memory, and the managed interactive concurrency limit stays installation-wide across them. Charts over multi-million-row snapshots spend more time in DuckDB; measure with your own dashboards using the [dashboard workload](../benchmarks/README.md#4-simulate-dashboard-viewers).

### Health checks and alerts

`deploy/compose/healthcheck.sh` checks the installation and sends a message when something breaks and again when it recovers ([ADR-0054](adr/0054-host-health-checks-and-alerts.md)). Each run checks:

- that the application answers at `https://<domain>/api/v1/health/ready`;
- that every long-running service is running and healthy;
- free disk space for Docker volumes and backups (alarm below 15%);
- that a backup newer than 26 hours exists;
- datasets whose most recent data load failed during the last week;
- that the TLS certificate is valid for at least 14 more days.

A lasting problem is reported once, not every five minutes. The thresholds can be changed with `DATAPIPETTE_HEALTH_MIN_FREE_PERCENT`, `DATAPIPETTE_HEALTH_MAX_BACKUP_HOURS`, and `DATAPIPETTE_HEALTH_MIN_CERT_DAYS`.

To receive alerts in Telegram, create a bot with [@BotFather](https://t.me/BotFather), send the bot any message, then open `https://api.telegram.org/bot<token>/getUpdates` and copy `message.chat.id`. Add both values to `.env`:

```bash
DATAPIPETTE_ALERT_TELEGRAM_BOT_TOKEN=<token from BotFather>
DATAPIPETTE_ALERT_TELEGRAM_CHAT_ID=<chat id>
```

`DATAPIPETTE_ALERT_WEBHOOK_URL` works as well or instead: it receives `{"text": "..."}`, the format of Slack, Mattermost, and Rocket.Chat incoming webhooks. Send a test message, then schedule the check every five minutes:

```bash
DATAPIPETTE_COMPOSE_FILES=deploy/compose/compose.yaml:deploy/compose/compose.production.yaml \
  deploy/compose/healthcheck.sh --test-alert

cat >/etc/cron.d/datapipette-healthcheck <<'EOF'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/5 * * * * root cd /opt/DataPipette && DATAPIPETTE_COMPOSE_FILES=deploy/compose/compose.yaml:deploy/compose/compose.production.yaml deploy/compose/healthcheck.sh --backup-root /var/backups/datapipette >>/var/log/datapipette-health.log 2>&1
EOF
```

The check runs on the server it watches, so it cannot report that the whole server is down. Add an external uptime monitor for `https://<domain>/healthz`, for example a free hosted uptime service.

### Move an evaluation installation to production

An installation that started from `compose.yaml` alone keeps its data. First check `.env`:

1. Create a backup with `deploy/compose/backup.sh create /var/backups/datapipette`.
2. If `DATAPIPETTE_CONNECTION_ENCRYPTION_KEY` is still the development value, rotate it. The command stops the application, re-encrypts every saved source password in one transaction, writes the new key into `.env`, starts the application, and proves every secret decrypts. If any secret cannot be decrypted, nothing is changed.

   ```bash
   DATAPIPETTE_NEW_CONNECTION_ENCRYPTION_KEY="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n')" \
     deploy/compose/backup.sh rotate-key --yes
   ```

   Backups made before the rotation can only be restored with the old key, so keep both keys until a new backup exists. Open table pages and pending download links signed with the old key stop working.
3. If `DATAPIPETTE_POSTGRES_PASSWORD` or the MinIO credentials are still the development values, replace them before exposing the server. PostgreSQL applies its password only when the volume is first created, so also run `ALTER USER datapipette PASSWORD '<new value>'` in the `metadata-db` container before restarting; MinIO picks up new root credentials on restart.
4. Add `DATAPIPETTE_DOMAIN`, start with both Compose files as shown above, and create a new backup.

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

Run it daily from the repository directory with cron, for example at 03:00 server time (add `DATAPIPETTE_COMPOSE_FILES` as shown in [Daily backup](#daily-backup) for a production installation):

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

The single-server overlay covers TLS, closed ports, production settings, required secrets, key rotation, and scheduled backups. It still keeps every component on one host and one disk. For larger or regulated installations, also plan for:

- managed or independently backed-up PostgreSQL, Redis, and S3-compatible storage;
- a production secret manager or Docker/Kubernetes secret delivery instead of `.env`;
- immutable published images rather than server-side source builds;
- off-host backup replication, regular restore drills, metrics, logs, alerts, and capacity benchmarks.

Production mode intentionally rejects the built-in development encryption key, local object storage, and non-HTTPS S3 endpoints other than the embedded private-network MinIO.
