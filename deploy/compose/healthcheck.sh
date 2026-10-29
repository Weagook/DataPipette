#!/usr/bin/env bash
# Check a DataPipette Compose installation and alert when its state changes.
#
# Run it every few minutes from cron. It checks that the application answers,
# that every long-running service is up and healthy, free disk space, the age
# of the newest backup, datasets whose latest data load failed, and the TLS
# certificate. A message is sent only when the set of problems changes, so a
# lasting problem does not repeat every run, and a recovery is announced once.
#
# Alerts go to Telegram and/or a webhook configured in the environment file:
#   DATAPIPETTE_ALERT_TELEGRAM_BOT_TOKEN and DATAPIPETTE_ALERT_TELEGRAM_CHAT_ID
#   DATAPIPETTE_ALERT_WEBHOOK_URL  (receives JSON {"text": "..."}; works with
#                                   Slack, Mattermost, and Rocket.Chat hooks)
# Without them, problems are only printed and the exit status is 1.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  deploy/compose/healthcheck.sh [--backup-root <dir>] [--state-file <path>] [--test-alert]

Environment:
  DATAPIPETTE_ENV_FILE        Compose environment file (default: <repository>/.env)
  DATAPIPETTE_COMPOSE_FILES   Colon-separated Compose files relative to the repository
  DATAPIPETTE_HEALTH_MIN_FREE_PERCENT   Free disk space alarm threshold (default 15)
  DATAPIPETTE_HEALTH_MAX_BACKUP_HOURS   Backup age alarm threshold (default 26)
  DATAPIPETTE_HEALTH_MIN_CERT_DAYS      Certificate expiry alarm threshold (default 14)
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_dir}/../.." && pwd)"
env_file="${DATAPIPETTE_ENV_FILE:-${repository_root}/.env}"
compose=(docker compose --env-file "${env_file}")
IFS=: read -r -a compose_files <<<"${DATAPIPETTE_COMPOSE_FILES:-deploy/compose/compose.yaml}"
for compose_file in "${compose_files[@]}"; do
  compose+=(-f "${repository_root}/${compose_file}")
done
backup_root=""
state_file="/var/lib/datapipette-health/state"
test_alert=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup-root) backup_root="${2:?}"; shift 2 ;;
    --state-file) state_file="${2:?}"; shift 2 ;;
    --test-alert) test_alert=true; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done
min_free="${DATAPIPETTE_HEALTH_MIN_FREE_PERCENT:-15}"
max_backup_hours="${DATAPIPETTE_HEALTH_MAX_BACKUP_HOURS:-26}"
min_cert_days="${DATAPIPETTE_HEALTH_MIN_CERT_DAYS:-14}"

# Read one value from the environment file without executing it.
env_value() {
  [[ -f "${env_file}" ]] || return 0
  sed -n "s/^$1=//p" "${env_file}" | tail -n 1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

domain="$(env_value DATAPIPETTE_DOMAIN)"
installation="${domain:-$(hostname)}"
problems=()

# 1. The application answers through its entry point. A host often cannot
# reach its own public address (no NAT hairpin), so the check connects to the
# local gateway port while still using the domain for TLS and routing.
curl_target=()
if [[ -n "${domain}" ]]; then
  ready_url="https://${domain}/api/v1/health/ready"
  curl_target=(--resolve "${domain}:443:127.0.0.1")
else
  ready_url="http://127.0.0.1:$(env_value DATAPIPETTE_HTTP_PORT | grep . || echo 8080)/api/v1/health/ready"
fi
if ! curl --fail --silent --show-error --max-time 10 --output /dev/null \
  ${curl_target[@]+"${curl_target[@]}"} "${ready_url}" 2>/dev/null; then
  problems+=("Application is not answering at ${ready_url}")
fi

# 2. Long-running services are running and, where they have a health check, healthy.
for service in metadata-db redis minio api worker scheduler frontend; do
  state="$("${compose[@]}" ps --all --format '{{.State}} {{.Health}}' "${service}" 2>/dev/null | head -n 1)"
  case "${state}" in
    "running healthy" | "running ") ;;
    "") problems+=("Service ${service} does not exist") ;;
    *) problems+=("Service ${service} is ${state% }") ;;
  esac
done
if [[ -n "${domain}" ]]; then
  state="$("${compose[@]}" ps --all --format '{{.State}}' gateway 2>/dev/null | head -n 1)"
  [[ "${state}" == "running" ]] || problems+=("Service gateway is ${state:-missing}")
fi

# 3. Free disk space where Docker keeps volumes and where backups are written.
docker_root="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /)"
for path in "${docker_root}" ${backup_root:+"${backup_root}"}; do
  [[ -d "${path}" ]] || continue
  used="$(df -P "${path}" | awk 'NR == 2 { gsub("%", "", $5); print $5 }')"
  if [[ -n "${used}" ]] && (( 100 - used < min_free )); then
    problems+=("Only $((100 - used))% disk space left on $(df -P "${path}" | awk 'NR == 2 { print $6 }')")
  fi
done

# 4. A complete backup set newer than the threshold exists.
if [[ -n "${backup_root}" ]]; then
  newest="$(find "${backup_root}" -maxdepth 1 -type d -name 'datapipette-*' ! -name '*.partial' \
    -mmin "-$((max_backup_hours * 60))" 2>/dev/null | head -n 1)"
  [[ -n "${newest}" ]] || problems+=("No backup newer than ${max_backup_hours} hours in ${backup_root}")
fi

# 5. Datasets whose most recent finished data load failed in the last week.
# shellcheck disable=SC2016
if failed="$("${compose[@]}" exec -T metadata-db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At' 2>/dev/null <<'SQL'
SELECT coalesce(string_agg(name, ', ' ORDER BY name), '') FROM (
  SELECT DISTINCT ON (r.dataset_id) d.name, r.state
  FROM dataset_refresh_runs r JOIN datasets d ON d.id = r.dataset_id
  WHERE r.finished_at > now() - interval '7 days'
  ORDER BY r.dataset_id, r.finished_at DESC
) latest WHERE state = 'failed';
SQL
)"; then
  [[ -z "${failed}" ]] || problems+=("Latest data load failed: ${failed}")
else
  problems+=("Metadata database cannot be queried")
fi

# 6. The public certificate is not about to expire.
if [[ -n "${domain}" ]] && command -v openssl >/dev/null; then
  certificate="$(openssl s_client -connect 127.0.0.1:443 -servername "${domain}" </dev/null 2>/dev/null \
    | openssl x509 2>/dev/null || true)"
  if [[ -z "${certificate}" ]]; then
    problems+=("TLS certificate for ${domain} cannot be read")
  elif ! openssl x509 -noout -checkend "$((min_cert_days * 86400))" <<<"${certificate}" >/dev/null; then
    problems+=("TLS certificate for ${domain} expires $(openssl x509 -noout -enddate <<<"${certificate}" | cut -d= -f2)")
  fi
fi

send() {
  local text="$1" sent=false
  local token chat webhook
  token="$(env_value DATAPIPETTE_ALERT_TELEGRAM_BOT_TOKEN)"
  chat="$(env_value DATAPIPETTE_ALERT_TELEGRAM_CHAT_ID)"
  webhook="$(env_value DATAPIPETTE_ALERT_WEBHOOK_URL)"
  if [[ -n "${token}" && -n "${chat}" ]]; then
    curl --fail --silent --max-time 15 --output /dev/null \
      --data-urlencode "chat_id=${chat}" --data-urlencode "text=${text}" \
      "https://api.telegram.org/bot${token}/sendMessage" && sent=true
  fi
  if [[ -n "${webhook}" ]]; then
    local escaped
    escaped="$(printf '%s' "${text}" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | awk 'BEGIN { ORS = "\\n" } { print }')"
    curl --fail --silent --max-time 15 --output /dev/null \
      -H 'Content-Type: application/json' --data "{\"text\":\"${escaped%\\n}\"}" "${webhook}" && sent=true
  fi
  [[ "${sent}" == true ]] || printf 'No alert channel delivered the message.\n' >&2
}

if [[ "${test_alert}" == true ]]; then
  send "DataPipette ${installation}: test alert from healthcheck.sh"
  exit 0
fi

current="$(printf '%s\n' "${problems[@]+"${problems[@]}"}" | sed '/^$/d' | sort)"
mkdir -p "$(dirname "${state_file}")"
previous="$(cat "${state_file}" 2>/dev/null || true)"
if [[ "${current}" != "${previous}" ]]; then
  if [[ -n "${current}" ]]; then
    send "DataPipette ${installation}: problems found
- ${current//$'\n'/$'\n'- }"
  else
    send "DataPipette ${installation}: all checks pass again"
  fi
  printf '%s' "${current}" >"${state_file}"
fi

if [[ -n "${current}" ]]; then
  printf '%s\n' "${current}"
  exit 1
fi
printf 'All checks pass\n'
