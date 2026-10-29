# ADR-0054: Host health checks and change-based alerts

- Status: Accepted
- Date: 2026-10-30

## Context

A single-server installation (ADR-0053) had no way to tell anyone that something broke. A failed nightly backup, a dataset whose scheduled load keeps failing, a full disk, or a stopped container went unnoticed until a user complained. The intended operators are small teams without a monitoring stack, so the first step must work with what the server already has.

## Decision

- Ship `deploy/compose/healthcheck.sh`, run from cron every five minutes on the DataPipette host. It checks:
  - the public readiness endpoint;
  - that every long-running service is running and healthy;
  - free disk space for Docker data and backups;
  - the age of the newest complete backup set;
  - datasets whose most recent finished data load failed during the last week;
  - the remaining validity of the TLS certificate.
- Alert through Telegram (bot token and chat ID) and/or a generic webhook that receives `{"text": ...}`, read from the existing environment file. No new service or dependency is added: the script uses Bash, curl, openssl, and Docker Compose.
- Alert on change only. The script stores the sorted set of current problems, sends a message when that set changes, and announces recovery once. A lasting problem therefore does not repeat every run.
- Read failed loads directly from metadata PostgreSQL inside the `metadata-db` container. No credentials leave the host, and no API session is needed.

## Consequences

- Operators learn about the common failures within five minutes without running a metrics system.
- The check cannot report a server that is completely down. The deployment guide recommends an external uptime monitor for `/healthz`.
- There are no historical metrics or graphs. A Prometheus-compatible metrics endpoint and dashboards remain future work.

## Alternatives considered

- **Prometheus, Alertmanager, and Grafana in Compose:** rejected for this step. They add three services, configuration, and storage that a small installation must maintain, while the immediate need is a message when something breaks.
- **An alerting feature inside the application:** rejected because the application cannot report its own outage, and backups, disk space, and container state live outside it.
