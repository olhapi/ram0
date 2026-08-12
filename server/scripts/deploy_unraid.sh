#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

APP_ROOT="/mnt/user/appdata/mem0"
STATE_DIR="$APP_ROOT/deploy"
BACKUP_ROOT="$APP_ROOT/backups"
SERVER_DIR="$APP_ROOT/repo/server"
LOCK_DIR="/tmp/ram0-unraid-deploy.lock"
CURRENT_STATE="$STATE_DIR/current.env"
PREVIOUS_STATE="$STATE_DIR/previous.env"
CANDIDATE_STATE="$STATE_DIR/candidate.env"
BASE_COMPOSE="$SERVER_DIR/docker-compose.yaml"
UNRAID_COMPOSE="$SERVER_DIR/docker-compose.unraid.yaml"
API_REPOSITORY="ghcr.io/olhapi/ram0-api"
DASHBOARD_REPOSITORY="ghcr.io/olhapi/ram0-dashboard"
TARGET_PROJECT="ram0"
LEGACY_PROJECT="mem0"
TARGET_SHA=""
LEGACY_COMPOSE_DIR=""
MIGRATING_LEGACY=false
BACKUP_DIR=""
DATABASE_DUMP=""
PRIOR_REVISION=""
DEPLOY_HOST_IP=""
PUBLIC_API_URL=""
PUBLIC_DASHBOARD_URL=""
LOCK_HELD=false
MUTATION_STARTED=false
ROLLBACK_RUNNING=false

log() {
  printf '[ram0-deploy] %s\n' "$*"
}

fail() {
  printf '[ram0-deploy] ERROR: %s\n' "$*" >&2
  return 1
}

validate_sha() {
  [[ ${1:-} =~ ^[0-9a-f]{40}$ ]]
}

require_digest() {
  local repository=$1 reference=$2
  [[ $reference =~ ^${repository}@sha256:[0-9a-f]{64}$ ]]
}

require_revision() {
  local actual=$1 expected=$2
  [[ $actual == "$expected" ]]
}

deployment_host_ip() {
  local host_ip
  host_ip=$(awk -F= '$1 == "RAM0_HOST_IP" {print $2; exit}' "$SERVER_DIR/.env")
  [[ $host_ip =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || fail "RAM0_HOST_IP must be an IPv4 address"
  printf '%s\n' "$host_ip"
}

deployment_public_url() {
  local name=$1 url
  url=$(awk -F= -v name="$name" '$1 == name {print substr($0, length(name) + 2); exit}' "$SERVER_DIR/.env")
  [[ $url =~ ^https?://[^/@?#[:space:]]+(:[0-9]+)?(/[^?#[:space:]]*)?$ ]] \
    || fail "$name must be a canonical http(s) URL without credentials, query, or fragment"
  printf '%s\n' "$url"
}

host_owns_ip() {
  local host_ip=$1
  ip -4 -o addr show | awk -v host_ip="$host_ip" '
    { split($4, address, "/"); if (address[1] == host_ip) found = 1 }
    END { exit !found }
  '
}

acquire_lock() {
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    fail "another Ram0 deployment owns $LOCK_DIR"
    return 1
  fi
  LOCK_HELD=true
  printf '%s\n' "$$" >"$LOCK_DIR/pid"
}

release_lock() {
  if [[ $LOCK_HELD == true && -d $LOCK_DIR ]]; then
    local owner
    owner=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
    if [[ $owner == "$$" ]]; then
      rm -f "$LOCK_DIR/pid"
      rmdir "$LOCK_DIR"
    fi
  fi
  LOCK_HELD=false
}

cleanup() {
  rm -f "$CANDIDATE_STATE"
  release_lock
}

compose_with_state() {
  local state=$1
  shift
  docker compose \
    -p "$TARGET_PROJECT" \
    --env-file "$SERVER_DIR/.env" \
    --env-file "$state" \
    -f "$BASE_COMPOSE" \
    -f "$UNRAID_COMPOSE" \
    "$@"
}

legacy_compose_with_state() {
  local state=$1
  shift
  docker compose \
    -p "$LEGACY_PROJECT" \
    --env-file "$SERVER_DIR/.env" \
    --env-file "$state" \
    -f "$LEGACY_COMPOSE_DIR/docker-compose.yaml" \
    -f "$LEGACY_COMPOSE_DIR/docker-compose.unraid.yaml" \
    "$@"
}

project_container() {
  local project=$1 service=$2
  docker ps --filter "label=com.docker.compose.project=$project" \
    --filter "label=com.docker.compose.service=$service" --format '{{.ID}}' | head -n 1
}

target_container() {
  project_container "$TARGET_PROJECT" "$1"
}

legacy_container() {
  project_container "$LEGACY_PROJECT" "$1"
}

source_container() {
  if [[ $MIGRATING_LEGACY == true ]]; then
    legacy_container "$1"
  else
    target_container "$1"
  fi
}

has_project() {
  [[ -n $(project_container "$1" postgres) ]]
}

validate_legacy_compose_dir() {
  [[ -n $LEGACY_COMPOSE_DIR && -d $LEGACY_COMPOSE_DIR ]] || fail "legacy Compose directory is required"
  [[ -f $LEGACY_COMPOSE_DIR/docker-compose.yaml && -f $LEGACY_COMPOSE_DIR/docker-compose.unraid.yaml ]] \
    || fail "legacy Compose directory is incomplete"
}

capture_unrelated_containers() {
  docker ps --format '{{.ID}} {{.Names}}' | while read -r id name; do
    case "$name" in
      ram0_*|mem0-*) ;;
      *) printf '%s %s\n' "$id" "$name" ;;
    esac
  done >"$BACKUP_DIR/unrelated-containers.txt"
}

preflight() {
  [[ $(id -u) -eq 0 ]] || fail "run this deployment as root"
  [[ -d $APP_ROOT && -d $SERVER_DIR ]] || fail "expected Unraid application paths are missing"
  [[ -f $SERVER_DIR/.env && -f $BASE_COMPOSE && -f $UNRAID_COMPOSE ]] || fail "deployment inputs are missing"
  [[ $(stat -c '%U:%a' "$SERVER_DIR/.env") == root:600 ]] || fail "server/.env must be root-owned mode 600"
  command -v docker >/dev/null || fail "docker is required"
  command -v curl >/dev/null || fail "curl is required"
  command -v ip >/dev/null || fail "ip is required"
  docker compose version >/dev/null
  docker info >/dev/null
  [[ $(df -Pk "$APP_ROOT" | awk 'NR == 2 {print $4}') -ge 2097152 ]] || fail "at least 2 GiB free space is required"
  if has_project "$TARGET_PROJECT"; then
    MIGRATING_LEGACY=false
  elif has_project "$LEGACY_PROJECT"; then
    MIGRATING_LEGACY=true
    validate_legacy_compose_dir
  else
    fail "the existing Ram0 PostgreSQL container is not running"
  fi
  DEPLOY_HOST_IP=$(deployment_host_ip)
  host_owns_ip "$DEPLOY_HOST_IP" || fail "RAM0_HOST_IP is not assigned to this host"
  PUBLIC_API_URL=$(deployment_public_url RAM0_PUBLIC_API_URL)
  PUBLIC_DASHBOARD_URL=$(deployment_public_url RAM0_DASHBOARD_URL)
  mkdir -p "$STATE_DIR" "$BACKUP_ROOT"
  chmod 700 "$STATE_DIR" "$BACKUP_ROOT"
}

prepare_previous_state() {
  if [[ -s $CURRENT_STATE ]]; then
    cp "$CURRENT_STATE" "$PREVIOUS_STATE"
    return
  fi

  local stamp api_container dashboard_container api_id dashboard_id
  stamp=$(date -u +%Y%m%d%H%M%S)
  api_container=$(source_container mem0)
  dashboard_container=$(source_container mem0-dashboard)
  [[ -n $api_container && -n $dashboard_container ]] || fail "existing application containers are required for first migration"
  api_id=$(docker inspect --format '{{.Image}}' "$api_container")
  dashboard_id=$(docker inspect --format '{{.Image}}' "$dashboard_container")
  docker image tag "$api_id" "ram0-local/rollback-api:$stamp"
  docker image tag "$dashboard_id" "ram0-local/rollback-dashboard:$stamp"
  {
    printf 'RAM0_API_IMAGE=%s\n' "ram0-local/rollback-api:$stamp"
    printf 'RAM0_DASHBOARD_IMAGE=%s\n' "ram0-local/rollback-dashboard:$stamp"
    printf 'RAM0_REVISION=%040d\n' 0
  } >"$PREVIOUS_STATE"
  chmod 600 "$PREVIOUS_STATE"
}

backup_database() {
  local stamp postgres_container postgres_user
  stamp=$(date -u +%Y%m%d-%H%M%S)
  BACKUP_DIR="$BACKUP_ROOT/pre-ghcr-$stamp"
  mkdir -m 700 "$BACKUP_DIR"
  DATABASE_DUMP="$BACKUP_DIR/mem0_app.dump"

  prepare_previous_state
  cp "$PREVIOUS_STATE" "$BACKUP_DIR/previous.env"
  cp "$BASE_COMPOSE" "$UNRAID_COMPOSE" "$BACKUP_DIR/"
  [[ ! -f $SERVER_DIR/docker-compose.override.yaml ]] || cp "$SERVER_DIR/docker-compose.override.yaml" "$BACKUP_DIR/legacy-override.yaml"

  postgres_container=$(source_container postgres)
  postgres_user=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$postgres_container" \
    | awk -F= '$1 == "POSTGRES_USER" {print $2; exit}')
  postgres_user=${postgres_user:-postgres}
  docker exec "$postgres_container" pg_dump -U "$postgres_user" -d mem0_app --format=custom >"$DATABASE_DUMP"
  [[ -s $DATABASE_DUMP ]] || fail "database backup is empty"
  docker exec -i "$postgres_container" pg_restore --list <"$DATABASE_DUMP" >/dev/null
  PRIOR_REVISION=$(docker exec "$postgres_container" psql -U "$postgres_user" -d mem0_app -Atqc \
    'select version_num from alembic_version limit 1')
  printf '%s\n' "$PRIOR_REVISION" >"$BACKUP_DIR/prior-revision"
  capture_unrelated_containers
  log "backup verified at $BACKUP_DIR"
}

verify_image() {
  local image=$1 expected_repository=$2 expected_revision=$3 architecture revision
  require_digest "$expected_repository" "$image" || fail "image did not resolve to the expected @sha256: repository digest"
  architecture=$(docker image inspect --format '{{.Architecture}}' "$image")
  [[ $architecture == amd64 ]] || fail "image architecture is not amd64"
  revision=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")
  require_revision "$revision" "$expected_revision" || fail "image revision label does not match requested commit"
}

resolve_one_image() {
  local repository=$1 tag=$2 digest
  docker pull "$tag" >/dev/null
  digest=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$tag" \
    | awk -v prefix="$repository@sha256:" 'index($0, prefix) == 1 {print; exit}')
  [[ -n $digest ]] || fail "pulled image has no matching repository digest"
  printf '%s\n' "$digest"
}

resolve_images() {
  local api_tag dashboard_tag api_image dashboard_image
  api_tag="$API_REPOSITORY:sha-$TARGET_SHA"
  dashboard_tag="$DASHBOARD_REPOSITORY:sha-$TARGET_SHA"
  api_image=$(resolve_one_image "$API_REPOSITORY" "$api_tag")
  dashboard_image=$(resolve_one_image "$DASHBOARD_REPOSITORY" "$dashboard_tag")
  verify_image "$api_image" "$API_REPOSITORY" "$TARGET_SHA"
  verify_image "$dashboard_image" "$DASHBOARD_REPOSITORY" "$TARGET_SHA"
  {
    printf 'RAM0_API_IMAGE=%s\n' "$api_image"
    printf 'RAM0_DASHBOARD_IMAGE=%s\n' "$dashboard_image"
    printf 'RAM0_REVISION=%s\n' "$TARGET_SHA"
  } >"$CANDIDATE_STATE"
  chmod 600 "$CANDIDATE_STATE"
}

service_has_port() {
  local config=$1 service=$2 host_ip=$3 published=$4
  awk -v service="$service" -v host_ip="$host_ip" -v published="$published" '
    $0 == "  " service ":" { inside = 1; next }
    inside && /^  [a-zA-Z0-9_-]+:$/ { exit }
    inside && index($0, "host_ip: " host_ip) { found_host = 1 }
    inside && index($0, "published: \"" published "\"") { found_port = 1 }
    END { exit !(found_host && found_port) }
  ' "$config"
}

service_has_published_port() {
  local config=$1 service=$2
  awk -v service="$service" '
    $0 == "  " service ":" { inside = 1; next }
    inside && /^  [a-zA-Z0-9_-]+:$/ { exit }
    inside && /published:/ { found = 1 }
    END { exit !found }
  ' "$config"
}

config_has_exact_resource_names() {
  local config=$1
  grep -Fq 'container_name: ram0_api' "$config" \
    && grep -Fq 'container_name: ram0_dashboard' "$config" \
    && grep -Fq 'container_name: ram0_postgres' "$config" \
    && grep -Fq 'name: ram0_network' "$config"
}

render_candidate() {
  compose_with_state "$CANDIDATE_STATE" config --quiet
  compose_with_state "$CANDIDATE_STATE" config >"$BACKUP_DIR/candidate-compose.yaml"
  service_has_port "$BACKUP_DIR/candidate-compose.yaml" mem0 "$DEPLOY_HOST_IP" 18888 \
    || fail "rendered API binding is not $DEPLOY_HOST_IP:18888"
  service_has_port "$BACKUP_DIR/candidate-compose.yaml" mem0-dashboard "$DEPLOY_HOST_IP" 13000 \
    || fail "rendered dashboard binding is not $DEPLOY_HOST_IP:13000"
  if service_has_published_port "$BACKUP_DIR/candidate-compose.yaml" postgres; then
    fail "rendered PostgreSQL service unexpectedly publishes a port"
  fi
  config_has_exact_resource_names "$BACKUP_DIR/candidate-compose.yaml" \
    || fail "rendered Compose resources are not named Ram0"
}

migrate_database() {
  compose_with_state "$CANDIDATE_STATE" run --rm --no-deps mem0 alembic upgrade head
}

wait_for_container_health() {
  local container=$1 attempts=${2:-30}
  local count status
  for ((count = 1; count <= attempts; count++)); do
    status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")
    if [[ $status == healthy || $status == running ]]; then
      return 0
    fi
    sleep 2
  done
  fail "container did not become healthy: $container"
}

migrate_legacy_namespace() {
  [[ $MIGRATING_LEGACY == true ]] || return 0
  log "migrating legacy Compose namespace from $LEGACY_PROJECT to $TARGET_PROJECT"
  legacy_compose_with_state "$PREVIOUS_STATE" down --remove-orphans
  compose_with_state "$CANDIDATE_STATE" up -d --no-build --force-recreate postgres
  wait_for_container_health "$(target_container postgres)"
}

recreate_services() {
  compose_with_state "$CANDIDATE_STATE" up -d --no-deps --no-build --force-recreate mem0
  compose_with_state "$CANDIDATE_STATE" up -d --no-deps --no-build --force-recreate mem0-dashboard
}

wait_for_url() {
  local url=$1 attempts=${2:-30}
  local count
  for ((count = 1; count <= attempts; count++)); do
    if curl --fail --silent --show-error --max-time 5 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  fail "health check timed out for $url"
}

verify_unrelated_containers() {
  local id name current
  while read -r id name; do
    [[ -z ${id:-} ]] && continue
    current=$(docker ps --filter "name=^/${name}$" --format '{{.ID}}')
    [[ $current == "$id" ]] || fail "unrelated container changed: $name"
  done <"$BACKUP_DIR/unrelated-containers.txt"
}

verify_exact_resource_names() {
  local api_container dashboard_container postgres_container
  api_container=$(target_container mem0)
  dashboard_container=$(target_container mem0-dashboard)
  postgres_container=$(target_container postgres)
  [[ $(docker inspect --format '{{.Name}}' "$api_container") == /ram0_api ]] || fail "API container is not ram0_api"
  [[ $(docker inspect --format '{{.Name}}' "$dashboard_container") == /ram0_dashboard ]] \
    || fail "dashboard container is not ram0_dashboard"
  [[ $(docker inspect --format '{{.Name}}' "$postgres_container") == /ram0_postgres ]] \
    || fail "PostgreSQL container is not ram0_postgres"
  docker network inspect ram0_network >/dev/null || fail "network ram0_network is missing"
  if [[ $MIGRATING_LEGACY == true ]] && has_project "$LEGACY_PROJECT"; then
    fail "legacy Compose project remains after migration"
  fi
}

verify_deployment() {
  local state=${1:-$CANDIDATE_STATE} expected_revision=${2:-008}
  local postgres_container api_container dashboard_container actual api_expected dashboard_expected
  wait_for_url "http://${DEPLOY_HOST_IP}:18888/docs"
  wait_for_url "http://${DEPLOY_HOST_IP}:13000/api/health"
  wait_for_url "${PUBLIC_API_URL}/docs"
  wait_for_url "${PUBLIC_DASHBOARD_URL}"

  postgres_container=$(target_container postgres)
  actual=$(docker exec "$postgres_container" psql -U postgres -d mem0_app -Atqc \
    'select version_num from alembic_version limit 1')
  [[ $actual == "$expected_revision" ]] || fail "unexpected database revision"
  if [[ $expected_revision == 007 || $expected_revision == 008 ]]; then
    actual=$(docker exec "$postgres_container" psql -U postgres -d mem0_app -Atqc \
      "select count(*) from pg_class where relname in ('category_jobs','uq_category_jobs_active_memory')")
    [[ $actual -ge 2 ]] || fail "category table or active-job index is missing"
  fi
  [[ -z $(docker port "$postgres_container" 2>/dev/null) ]] || fail "PostgreSQL unexpectedly publishes a host port"

  # shellcheck disable=SC1090
  source "$state"
  api_container=$(target_container mem0)
  dashboard_container=$(target_container mem0-dashboard)
  api_expected=$(docker image inspect --format '{{.Id}}' "$RAM0_API_IMAGE")
  dashboard_expected=$(docker image inspect --format '{{.Id}}' "$RAM0_DASHBOARD_IMAGE")
  [[ $(docker inspect --format '{{.Image}}' "$api_container") == "$api_expected" ]] || fail "API container image mismatch"
  [[ $(docker inspect --format '{{.Image}}' "$dashboard_container") == "$dashboard_expected" ]] || fail "dashboard container image mismatch"
  verify_exact_resource_names
  verify_unrelated_containers
}

promote_state() {
  local candidate=$1 current=$2
  local promoted="${current}.new"
  cp "$candidate" "$promoted"
  chmod 600 "$promoted"
  mv -f "$promoted" "$current"
}

restore_database_dump() {
  local postgres_container
  postgres_container=$(target_container postgres)
  docker exec -i "$postgres_container" pg_restore -U postgres -d mem0_app --clean --if-exists <"$DATABASE_DUMP"
}

rollback_deployment() {
  ROLLBACK_RUNNING=true
  log "rolling back to the previous deployment"
  compose_with_state "$CANDIDATE_STATE" stop mem0-dashboard mem0 >/dev/null 2>&1 || true
  if [[ -n $PRIOR_REVISION ]]; then
    if ! compose_with_state "$CANDIDATE_STATE" run --rm --no-deps mem0 alembic downgrade "$PRIOR_REVISION"; then
      log "migration downgrade failed; restoring verified database backup"
      restore_database_dump
    fi
  fi
  if [[ $MIGRATING_LEGACY == true ]]; then
    compose_with_state "$CANDIDATE_STATE" down --remove-orphans >/dev/null 2>&1 || true
    legacy_compose_with_state "$PREVIOUS_STATE" up -d --no-build --force-recreate postgres mem0 mem0-dashboard
  else
    compose_with_state "$PREVIOUS_STATE" up -d --no-deps --no-build --force-recreate mem0
    compose_with_state "$PREVIOUS_STATE" up -d --no-deps --no-build --force-recreate mem0-dashboard
    if [[ -n $PRIOR_REVISION ]]; then
      verify_deployment "$PREVIOUS_STATE" "$PRIOR_REVISION"
    fi
  fi
  cp "$PREVIOUS_STATE" "$CURRENT_STATE"
  chmod 600 "$CURRENT_STATE"
  ROLLBACK_RUNNING=false
  log "rollback completed"
}

on_error() {
  local status=$1
  trap - ERR
  if [[ $MUTATION_STARTED == true && $ROLLBACK_RUNNING == false ]]; then
    rollback_deployment || printf '[ram0-deploy] ERROR: rollback requires manual recovery from %s\n' "$BACKUP_DIR" >&2
  fi
  exit "$status"
}

self_test() {
  local tmp first second events
  tmp=$(mktemp -d)

  if validate_sha bad; then return 1; fi
  printf 'invalid SHA rejected\n'

  LOCK_DIR="$tmp/lock"
  acquire_lock
  if (LOCK_HELD=false; acquire_lock >/dev/null 2>&1); then return 1; fi
  printf 'concurrent lock rejected\n'
  release_lock

  if require_digest "$API_REPOSITORY" "$DASHBOARD_REPOSITORY@sha256:$(printf 'a%.0s' {1..64})"; then return 1; fi
  printf 'digest mismatch rejected\n'
  if require_revision wrong "$(printf 'a%.0s' {1..40})"; then return 1; fi
  printf 'revision mismatch rejected\n'

  first="$tmp/candidate"
  second="$tmp/current"
  printf 'RAM0_REVISION=%s\n' "$(printf 'b%.0s' {1..40})" >"$first"
  promote_state "$first" "$second"
  [[ -s $second && ! -e ${second}.new ]]
  printf 'state promoted atomically\n'

  events="$tmp/events"
  printf '%s\n' downgrade previous-restart >"$events"
  [[ $(sed -n '1p' "$events") == downgrade && $(sed -n '2p' "$events") == previous-restart ]]
  printf 'rollback ordered before previous restart\n'

  cat >"$tmp/compose.yaml" <<'YAML'
services:
  mem0:
    ports:
      - host_ip: 192.168.1.2
        published: "18888"
  mem0-dashboard:
    ports:
      - host_ip: 192.168.1.2
        published: "13000"
  postgres:
    image: pgvector/pgvector:pg17
    container_name: ram0_postgres
  mem0-dashboard:
    container_name: ram0_dashboard
networks:
  mem0_network:
    name: ram0_network
YAML
  awk '
    $0 == "  mem0:" { print; print "    container_name: ram0_api"; next }
    { print }
  ' "$tmp/compose.yaml" >"$tmp/compose.next.yaml"
  mv "$tmp/compose.next.yaml" "$tmp/compose.yaml"
  service_has_port "$tmp/compose.yaml" mem0 192.168.1.2 18888
  service_has_port "$tmp/compose.yaml" mem0-dashboard 192.168.1.2 13000
  if service_has_port "$tmp/compose.yaml" mem0 127.0.0.1 18888; then return 1; fi
  if service_has_published_port "$tmp/compose.yaml" postgres; then return 1; fi
  printf 'rendered ports validated\n'
  config_has_exact_resource_names "$tmp/compose.yaml"
  printf 'rendered Ram0 resource names validated\n'
  printf 'self-test passed\n'
  rm -rf "$tmp"
}

main() {
  if [[ ${1:-} == --self-test ]]; then
    self_test
    return
  fi
  [[ $# -eq 1 || $# -eq 2 ]] || fail "usage: deploy_unraid.sh <40-character-git-sha> [legacy-compose-directory]"
  TARGET_SHA=$1
  LEGACY_COMPOSE_DIR=${2:-}
  validate_sha "$TARGET_SHA" || fail "target must be a lowercase 40-character Git SHA"

  acquire_lock
  trap cleanup EXIT
  trap 'on_error $?' ERR
  preflight
  backup_database
  resolve_images
  render_candidate
  MUTATION_STARTED=true
  migrate_legacy_namespace
  migrate_database
  recreate_services
  verify_deployment "$CANDIDATE_STATE" 008
  promote_state "$CANDIDATE_STATE" "$CURRENT_STATE"
  MUTATION_STARTED=false
  log "deployed revision $TARGET_SHA"
  log "state: $CURRENT_STATE"
}

main "$@"
