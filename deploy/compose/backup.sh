#!/usr/bin/env bash
# Single-quoted commands are expanded by the container shell, not this one.
# shellcheck disable=SC2016

# Back up and restore a DataPipette Compose installation.
#
# A backup set contains a PostgreSQL custom-format dump of the metadata
# database, a checksummed copy of the private object bucket, and a manifest
# with the connection-encryption-key fingerprint. The key itself is never
# written to the backup set and must be stored separately.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  deploy/compose/backup.sh create <backup-root> [--keep N]
  deploy/compose/backup.sh verify <backup-set>
  deploy/compose/backup.sh restore <backup-set> --yes
  deploy/compose/backup.sh rotate-key --yes

Environment:
  DATAPIPETTE_ENV_FILE       Compose environment file (default: <repository>/.env)
  DATAPIPETTE_COMPOSE_FILES  Colon-separated Compose files relative to the repository
                             (default: deploy/compose/compose.yaml; add
                             deploy/compose/compose.production.yaml in production)
  DATAPIPETTE_NEW_CONNECTION_ENCRYPTION_KEY
                             New key for rotate-key
EOF
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_dir}/../.." && pwd)"
env_file="${DATAPIPETTE_ENV_FILE:-${repository_root}/.env}"
compose=(docker compose --env-file "${env_file}")
IFS=: read -r -a compose_files <<<"${DATAPIPETTE_COMPOSE_FILES:-deploy/compose/compose.yaml}"
for compose_file in "${compose_files[@]}"; do
  compose+=(-f "${repository_root}/${compose_file}")
done
format_version="datapipette-backup-v1"
application_services=(frontend api worker scheduler)

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

fail() {
  log "ERROR: $*"
  exit 1
}

sha256_tool() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$@"
  else
    shasum -a 256 "$@"
  fi
}

# Run the backend image with a host directory mounted at /backup. The container
# runs as the invoking host user so backup files keep ordinary host ownership.
backup_tool() {
  local mount_dir="$1"
  shift
  "${compose[@]}" run --rm --no-deps -T \
    --user "$(id -u):$(id -g)" \
    --volume "${mount_dir}:/backup" \
    api datapipette-backup "$@"
}

metadata_exec() {
  "${compose[@]}" exec -T metadata-db sh -c "$1"
}

manifest_value() {
  local manifest="$1" key="$2"
  sed -n "s/^${key}=//p" "${manifest}" | head -n 1
}

require_running() {
  local service
  for service in "$@"; do
    if [[ -z "$("${compose[@]}" ps --status running --quiet "${service}")" ]]; then
      fail "service ${service} is not running; start the stack before this command"
    fi
  done
}

create_backup() {
  local backup_root="" keep=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --keep)
        [[ $# -ge 2 && "$2" =~ ^[1-9][0-9]*$ ]] || fail "--keep requires a positive integer"
        keep="$2"
        shift 2
        ;;
      *)
        [[ -z "${backup_root}" ]] || fail "unexpected argument: $1"
        backup_root="$1"
        shift
        ;;
    esac
  done
  [[ -n "${backup_root}" ]] || { usage; exit 2; }
  [[ -f "${env_file}" ]] || fail "environment file ${env_file} does not exist"
  require_running metadata-db minio

  umask 077
  mkdir -p "${backup_root}"
  backup_root="$(cd "${backup_root}" && pwd)"
  local stamp set_dir work_dir
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  set_dir="${backup_root}/datapipette-${stamp}"
  work_dir="${set_dir}.partial"
  [[ ! -e "${set_dir}" && ! -e "${work_dir}" ]] || fail "${set_dir} already exists"
  mkdir "${work_dir}"
  trap 'rm -rf "${work_dir}"' EXIT

  log "Reading key fingerprint and schema revision"
  local fingerprint revision
  fingerprint="$(backup_tool "${work_dir}" key-fingerprint)"
  revision="$(metadata_exec 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version"')"

  # The dump comes first: active snapshots are never deleted, so every object
  # it references is still present when the bucket copy runs afterwards.
  log "Dumping metadata PostgreSQL"
  metadata_exec 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
    >"${work_dir}/metadata.dump"

  log "Copying private object storage"
  backup_tool "${work_dir}" objects-export --dir /backup/objects >&2

  local application_revision="unknown"
  if git -C "${repository_root}" rev-parse --verify HEAD >/dev/null 2>&1; then
    application_revision="$(git -C "${repository_root}" rev-parse HEAD)"
  fi
  cat >"${work_dir}/MANIFEST" <<EOF
format=${format_version}
created_at=${stamp}
application_revision=${application_revision}
alembic_revision=${revision}
key_fingerprint=${fingerprint}
EOF
  (cd "${work_dir}" && sha256_tool MANIFEST metadata.dump objects/objects.jsonl >SHA256SUMS)

  mv "${work_dir}" "${set_dir}"
  trap - EXIT
  log "Created ${set_dir}"

  if [[ -n "${keep}" ]]; then
    local sets=() stale
    while IFS= read -r stale; do
      sets+=("${stale}")
    done < <(find "${backup_root}" -mindepth 1 -maxdepth 1 -type d \
      -name 'datapipette-[0-9]*T[0-9]*Z' | sort -r)
    for stale in "${sets[@]:${keep}}"; do
      log "Removing expired backup ${stale}"
      rm -rf "${stale}"
    done
  fi
  printf '%s\n' "${set_dir}"
}

verify_backup() {
  local set_dir="$1"
  [[ -f "${set_dir}/MANIFEST" && -f "${set_dir}/SHA256SUMS" ]] || fail "${set_dir} is not a backup set"
  [[ "$(manifest_value "${set_dir}/MANIFEST" format)" == "${format_version}" ]] \
    || fail "unsupported backup format in ${set_dir}"
  log "Checking file checksums"
  (cd "${set_dir}" && sha256_tool -c --quiet SHA256SUMS) || fail "checksum verification failed"
  log "Checking object copies"
  backup_tool "${set_dir}" objects-verify --dir /backup/objects >&2
  log "Checking the metadata dump"
  "${compose[@]}" exec -T metadata-db pg_restore --list >/dev/null <"${set_dir}/metadata.dump" \
    || fail "metadata dump is unreadable"
  log "Backup set ${set_dir} is complete"
}

restore_backup() {
  local set_dir="" confirmed=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --yes) confirmed=true; shift ;;
      *)
        [[ -z "${set_dir}" ]] || fail "unexpected argument: $1"
        set_dir="$1"
        shift
        ;;
    esac
  done
  [[ -n "${set_dir}" ]] || { usage; exit 2; }
  [[ "${confirmed}" == true ]] \
    || fail "restore replaces all metadata, objects, and queued work; rerun with --yes"
  [[ -f "${env_file}" ]] || fail "environment file ${env_file} does not exist"
  set_dir="$(cd "${set_dir}" && pwd)"

  log "Starting storage services"
  "${compose[@]}" up -d --wait metadata-db redis minio
  verify_backup "${set_dir}"

  local expected actual
  expected="$(manifest_value "${set_dir}/MANIFEST" key_fingerprint)"
  actual="$(backup_tool "${set_dir}" key-fingerprint)"
  [[ "${expected}" == "${actual}" ]] || fail \
    "DATAPIPETTE_CONNECTION_ENCRYPTION_KEY in ${env_file} is not the key this backup was made with"

  log "Stopping application services"
  "${compose[@]}" stop "${application_services[@]}"

  log "Replacing metadata PostgreSQL"
  metadata_exec 'dropdb -U "$POSTGRES_USER" --if-exists --force "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
  metadata_exec 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error' \
    <"${set_dir}/metadata.dump"

  # Redis holds only the Celery transport and login throttling; durable run
  # state lives in PostgreSQL and recovers undispatched work.
  log "Clearing transient Redis state"
  "${compose[@]}" exec -T redis redis-cli FLUSHALL >/dev/null

  log "Replacing private object storage"
  backup_tool "${set_dir}" objects-import --dir /backup/objects --prune >&2

  log "Starting the application"
  "${compose[@]}" up -d --wait

  log "Checking stored connection secrets"
  backup_tool "${set_dir}" verify-key >&2
  log "Restored ${set_dir}"
}

# Re-encrypt stored source-connection secrets with a new key, then switch the
# environment file to that key. Backups made earlier still need the old key.
rotate_key() {
  [[ "${1:-}" == "--yes" && $# -eq 1 ]] \
    || fail "rotate-key changes how every connection secret is encrypted; rerun with --yes"
  [[ -f "${env_file}" ]] || fail "environment file ${env_file} does not exist"
  [[ -n "${DATAPIPETTE_NEW_CONNECTION_ENCRYPTION_KEY:-}" ]] \
    || fail "set DATAPIPETTE_NEW_CONNECTION_ENCRYPTION_KEY to the new key"
  grep -q '^DATAPIPETTE_CONNECTION_ENCRYPTION_KEY=' "${env_file}" \
    || fail "${env_file} has no DATAPIPETTE_CONNECTION_ENCRYPTION_KEY line to replace"
  require_running metadata-db

  log "Stopping application services"
  "${compose[@]}" stop "${application_services[@]}"
  log "Re-encrypting connection secrets"
  if ! "${compose[@]}" run --rm --no-deps -T \
    -e DATAPIPETTE_NEW_CONNECTION_ENCRYPTION_KEY \
    api datapipette-backup rotate-key >&2; then
    "${compose[@]}" up -d --wait
    fail "rotation failed; nothing was changed and the application was restarted"
  fi

  log "Writing the new key to ${env_file}"
  local updated="${env_file}.rotating"
  (
    umask 077
    awk -v key="${DATAPIPETTE_NEW_CONNECTION_ENCRYPTION_KEY}" \
      '/^DATAPIPETTE_CONNECTION_ENCRYPTION_KEY=/ { print "DATAPIPETTE_CONNECTION_ENCRYPTION_KEY=" key; next } { print }' \
      "${env_file}" >"${updated}"
  )
  mv "${updated}" "${env_file}"

  log "Starting the application"
  "${compose[@]}" up -d --wait
  "${compose[@]}" run --rm --no-deps -T api datapipette-backup verify-key >&2
  log "Key rotated. Store the new key separately and create a fresh backup."
}

command="${1:-}"
[[ $# -gt 0 ]] && shift
case "${command}" in
  create) create_backup "$@" ;;
  verify)
    [[ $# -eq 1 ]] || { usage; exit 2; }
    [[ -f "${env_file}" ]] || fail "environment file ${env_file} does not exist"
    require_running metadata-db
    verify_backup "$(cd "$1" && pwd)"
    ;;
  restore) restore_backup "$@" ;;
  rotate-key) rotate_key "$@" ;;
  -h | --help | help) usage ;;
  *) usage; exit 2 ;;
esac
