#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

ACTION="${1:-}"
shift || true

MOUNT_DIR_READONLY=""
MOUNT_DIR_RW=""

for arg in "$@"; do
  case "${arg}" in
    --mount-dir-readonly=*)
      MOUNT_DIR_READONLY="${arg#*=}"
      ;;
    --mount-dir-rw=*)
      MOUNT_DIR_RW="${arg#*=}"
      ;;
    *)
      ;;
  esac
done

if [[ "${ACTION}" != "build" && "${ACTION}" != "cleanup" ]]; then
  echo "Usage: ./deploy.sh <build|cleanup> [--mount-dir-readonly=/path] [--mount-dir-rw=/path]"
  exit 1
fi

function read_config_value() {
  local key="$1"
  python3 - <<PY
import sys
import yaml

with open("config/api.yaml", "r", encoding="utf-8") as handle:
    config = yaml.safe_load(handle)

value = config
for part in "${key}".split("."):
    value = value.get(part, {})

if isinstance(value, dict):
    sys.exit("Missing key: ${key}")

print(value)
PY
}

function prepare_mount_overrides() {
  local override_file="docker-compose.override.yml"
  rm -f "${override_file}"

  if [[ -z "${MOUNT_DIR_READONLY}" && -z "${MOUNT_DIR_RW}" ]]; then
    return
  fi

  local rw_suffix=""
  if [[ "$(uname -s)" == "Linux" ]]; then
    rw_suffix=":rshared"
  fi

  {
    echo "services:"
    echo "  agentum-api:"
    echo "    volumes:"
    if [[ -n "${MOUNT_DIR_READONLY}" ]]; then
      echo "      - ${MOUNT_DIR_READONLY}:/mounts/readonly:ro"
    fi
    if [[ -n "${MOUNT_DIR_RW}" ]]; then
      echo "      - ${MOUNT_DIR_RW}:/mounts${rw_suffix}"
    fi
  } > "${override_file}"
}

function render_ui_config() {
  cat > src/web_terminal_client/public/config.yaml <<EOF
server:
  port: ${WEB_PORT}
  host: "0.0.0.0"

api:
  base_url: "http://localhost:${API_PORT}"

ui:
  max_output_lines: 1000
  auto_scroll: true
EOF
}

function check_services() {
  local missing=0
  local running
  running="$(docker compose ps --status running --services || true)"
  for svc in agentum-api agentum-web; do
    if ! grep -q "${svc}" <<<"${running}"; then
      echo "Service not running: ${svc}"
      missing=1
    fi
  done
  return "${missing}"
}

if [[ "${ACTION}" == "cleanup" ]]; then
  docker compose down --remove-orphans
  rm -f docker-compose.override.yml
  if docker images --format '{{.Repository}}:{{.Tag}}' | grep -q '^agentum:'; then
    docker images --format '{{.Repository}}:{{.Tag}}' \
      | grep '^agentum:' \
      | xargs -r docker image rm
  fi
  echo "Cleanup complete."
  exit 0
fi

API_PORT="$(read_config_value 'api.external_port')"
WEB_PORT="$(read_config_value 'web.external_port')"

render_ui_config
prepare_mount_overrides

IMAGE_TAG="deploy-$(date +%Y%m%d%H%M%S)"
BACKUP_ENV="$(mktemp)"
ROLLBACK_ENV=0

cleanup() {
  if [[ "${ROLLBACK_ENV}" -eq 1 && -s "${BACKUP_ENV}" ]]; then
    cp "${BACKUP_ENV}" .env
    docker compose up -d --remove-orphans || true
  fi
  rm -f "${BACKUP_ENV}"
}

trap cleanup EXIT

if [[ -f .env ]]; then
  cp .env "${BACKUP_ENV}"
fi

echo "Building image agentum:${IMAGE_TAG}..."
docker build -t "agentum:${IMAGE_TAG}" .

ROLLBACK_ENV=1
cat > .env <<EOF
AGENTUM_IMAGE_TAG=${IMAGE_TAG}
AGENTUM_API_PORT=${API_PORT}
AGENTUM_WEB_PORT=${WEB_PORT}
EOF

echo "Starting containers with tag ${IMAGE_TAG}..."
docker compose up -d --remove-orphans

if ! check_services; then
  echo "Deployment failed, rolling back."
  exit 1
fi

ROLLBACK_ENV=0
echo "Deployment complete."
