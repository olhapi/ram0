#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: MIT

set -Eeuo pipefail
umask 077

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
COMPOSE_FILE="$SCRIPT_DIR/compose.yaml"
ENV_FILE=${RAM0_DEPLOY_ENV:-/mnt/user/appdata/mem0/supermemory.env}
LOCK_DIR=/tmp/ram0-supermemory-deploy.lock
CANDIDATE_PROJECT=ram0-supermemory-candidate
LIVE_PROJECT=ram0-supermemory
BACKUP_DIR=
MUTATION_STARTED=false
ROLLBACK_RUNNING=false
LOCK_HELD=false

log() {
  printf '[deploy-supermemory] %s\n' "$*"
}

fail() {
  printf '[deploy-supermemory] ERROR: %s\n' "$*" >&2
  return 1
}

validate_sha() {
  [[ ${1:-} =~ ^[0-9a-f]{40}$ ]]
}

validate_digest_ref() {
  [[ ${1:-} =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]]
}

legacy_application_database() {
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' ram0_api \
    | awk -F= '$1 == "APP_DB_NAME" {print $2; exit}'
}

compose_for() {
  local project=$1 host=$2 api_port=$3 graph_port=$4
  shift 4
  env \
    RAM0_COMPOSE_PROJECT="$project" \
    RAM0_HOST_IP="$host" \
    RAM0_API_PORT="$api_port" \
    RAM0_GRAPH_PORT="$graph_port" \
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

candidate_compose() {
  compose_for "$CANDIDATE_PROJECT" 127.0.0.1 28888 23000 "$@"
}

live_compose() {
  compose_for "$LIVE_PROJECT" "$RAM0_HOST_IP" 18888 13000 "$@"
}

acquire_lock() {
  mkdir "$LOCK_DIR" 2>/dev/null || fail "another deployment owns $LOCK_DIR"
  LOCK_HELD=true
  printf '%s\n' "$$" >"$LOCK_DIR/pid"
}

release_lock() {
  if [[ $LOCK_HELD == true && -d $LOCK_DIR && $(cat "$LOCK_DIR/pid" 2>/dev/null || true) == "$$" ]]; then
    rm -f "$LOCK_DIR/pid"
    rmdir "$LOCK_DIR"
  fi
  LOCK_HELD=false
}

load_environment() {
  [[ -f $ENV_FILE ]] || fail "deployment environment is missing: $ENV_FILE"
  [[ $(stat -c '%U:%a' "$ENV_FILE") == root:600 ]] || fail 'deployment environment must be root-owned mode 600'
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  export RAM0_HOST_IP RAM0_SUPERMEMORY_DATA_DIR RAM0_MIGRATION_DIR RAM0_RUNTIME_ENV_FILE
  export RAM0_ENGINE_IMAGE RAM0_GATEWAY_IMAGE RAM0_GRAPH_IMAGE RAM0_REVISION OPENAI_API_KEY
}

validate_plan() {
  local plan=$1 source records duplicates invalid accounts
  source=$(jq -r '.sourceCount' "$plan")
  records=$(jq -r '.records | length' "$plan")
  duplicates=$(jq -r '.exactDuplicates' "$plan")
  invalid=$(jq -r '.invalid | length' "$plan")
  accounts=$(jq -r '[.records[].metadata.ram0_account_ids[]] | unique | length' "$plan")
  [[ $source =~ ^[0-9]+$ && $records =~ ^[0-9]+$ && $duplicates =~ ^[0-9]+$ && $invalid == 0 ]] \
    || fail 'import plan counts are invalid'
  (( source == records + duplicates + invalid )) || fail 'import plan source accounting does not balance'
  [[ $accounts -eq 2 ]] || fail "import plan must contain exactly two source accounts (found $accounts)"
}

preflight() {
  [[ $(id -u) -eq 0 ]] || fail 'run this deployment as root on Unraid'
  for command in docker curl jq; do command -v "$command" >/dev/null || fail "$command is required"; done
  docker compose version >/dev/null
  docker info >/dev/null
  validate_sha "$RAM0_REVISION" || fail 'RAM0_REVISION must be a full lowercase Git SHA'
  validate_digest_ref "$RAM0_ENGINE_IMAGE" || fail 'RAM0_ENGINE_IMAGE must be an immutable digest reference'
  validate_digest_ref "$RAM0_GATEWAY_IMAGE" || fail 'RAM0_GATEWAY_IMAGE must be an immutable digest reference'
  validate_digest_ref "$RAM0_GRAPH_IMAGE" || fail 'RAM0_GRAPH_IMAGE must be an immutable digest reference'
  [[ $RAM0_HOST_IP =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || fail 'RAM0_HOST_IP must be an IPv4 address'
  [[ -d ${RAM0_LEGACY_COMPOSE_DIR:-} ]] || fail 'RAM0_LEGACY_COMPOSE_DIR is missing'
  [[ -f $RAM0_LEGACY_COMPOSE_DIR/docker-compose.yaml ]] || fail 'legacy base Compose file is missing'
  [[ -f $RAM0_LEGACY_COMPOSE_DIR/docker-compose.unraid.yaml ]] || fail 'legacy Unraid Compose file is missing'
  [[ -f ${RAM0_LEGACY_STATE_FILE:-} ]] || fail 'RAM0_LEGACY_STATE_FILE is missing'
  [[ -f $RAM0_MIGRATION_DIR/import-plan.json ]] || fail 'import-plan.json is missing'
  validate_plan "$RAM0_MIGRATION_DIR/import-plan.json"
  [[ -n $(docker ps --filter name='^/ram0_postgres$' --format '{{.ID}}') ]] || fail 'ram0_postgres is not running'
  mkdir -p "$RAM0_SUPERMEMORY_DATA_DIR" "$RAM0_MIGRATION_DIR" "$(dirname "$RAM0_RUNTIME_ENV_FILE")"
  chown 65532:65532 "$RAM0_SUPERMEMORY_DATA_DIR"
  chmod 700 "$RAM0_SUPERMEMORY_DATA_DIR"
  chown 1000:1000 "$RAM0_MIGRATION_DIR" "$RAM0_MIGRATION_DIR/import-plan.json"
  chmod 700 "$RAM0_MIGRATION_DIR"
  chmod 600 "$RAM0_MIGRATION_DIR/import-plan.json"
  if [[ -f $RAM0_MIGRATION_DIR/import-journal.jsonl ]]; then
    chown 1000:1000 "$RAM0_MIGRATION_DIR/import-journal.jsonl"
    chmod 600 "$RAM0_MIGRATION_DIR/import-journal.jsonl"
  fi
  chmod 700 "$(dirname "$RAM0_RUNTIME_ENV_FILE")"
  printf 'SUPERMEMORY_API_KEY=initializing\n' >"$RAM0_RUNTIME_ENV_FILE"
  chmod 600 "$RAM0_RUNTIME_ENV_FILE"
  candidate_compose config --quiet
  live_compose config --quiet
}

backup_legacy() {
  local stamp postgres_user postgres_db
  stamp=$(date -u +%Y%m%d-%H%M%S)
  BACKUP_DIR=/mnt/user/appdata/mem0/backups/pre-supermemory-$stamp
  mkdir -m 700 "$BACKUP_DIR"
  postgres_user=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' ram0_postgres \
    | awk -F= '$1 == "POSTGRES_USER" {print $2; exit}')
  postgres_db=$(legacy_application_database)
  postgres_user=${postgres_user:-postgres}
  [[ -n $postgres_db ]] || fail 'APP_DB_NAME is missing from ram0_api'
  docker exec ram0_postgres pg_dump -U "$postgres_user" -d "$postgres_db" --format=custom >"$BACKUP_DIR/ram0.dump"
  [[ -s $BACKUP_DIR/ram0.dump ]] || fail 'PostgreSQL backup is empty'
  docker exec -i ram0_postgres pg_restore --list <"$BACKUP_DIR/ram0.dump" >/dev/null
  cp "$RAM0_MIGRATION_DIR/import-plan.json" "$BACKUP_DIR/import-plan.json"
  cp "$RAM0_LEGACY_COMPOSE_DIR/docker-compose.yaml" "$RAM0_LEGACY_COMPOSE_DIR/docker-compose.unraid.yaml" "$BACKUP_DIR/"
  cp "$ENV_FILE" "$RAM0_LEGACY_STATE_FILE" "$BACKUP_DIR/"
  sha256sum "$BACKUP_DIR/ram0.dump" "$BACKUP_DIR/import-plan.json" >"$BACKUP_DIR/SHA256SUMS"
  log "verified source backup at $BACKUP_DIR"
}

wait_for_url() {
  local url=$1 attempts=${2:-60} count
  for ((count = 1; count <= attempts; count++)); do
    if curl --fail --silent --show-error --max-time 5 "$url" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  fail "health check timed out: $url"
}

start_candidate() {
  candidate_compose up -d engine
  local engine_id key count
  engine_id=$(candidate_compose ps -q engine)
  [[ -n $engine_id ]] || fail 'candidate engine did not start'
  for ((count = 1; count <= 60; count++)); do
    key=$(docker exec "$engine_id" cat /data/api-key 2>/dev/null || true)
    [[ -n $key ]] && break
    sleep 2
  done
  [[ -n ${key:-} ]] || fail 'candidate engine did not generate an API key'
  printf 'SUPERMEMORY_API_KEY=%s\n' "$key" >"$RAM0_RUNTIME_ENV_FILE"
  chmod 600 "$RAM0_RUNTIME_ENV_FILE"
  candidate_compose up -d --force-recreate gateway graph
  wait_for_url 'http://127.0.0.1:28888/health'
  "$SCRIPT_DIR/verify-stack.sh" http://127.0.0.1:28888 http://127.0.0.1:23000 "$RAM0_RUNTIME_ENV_FILE"
}

import_memories() {
  candidate_compose run --rm --no-deps gateway \
    bun /app/migrate.js import \
    --plan /migration/import-plan.json \
    --journal /migration/import-journal.jsonl \
    --base-url http://engine:6767
  local expected completed
  expected=$(jq -r '.records | length' "$RAM0_MIGRATION_DIR/import-plan.json")
  completed=$(jq -s '[.[].keys[]] | unique | length' "$RAM0_MIGRATION_DIR/import-journal.jsonl")
  [[ $completed -eq $expected ]] || fail "import journal covers $completed of $expected records"
  local count
  for ((count = 1; count <= 60; count++)); do
    if candidate_compose run --rm --no-deps gateway \
      bun /app/migrate.js verify \
      --plan /migration/import-plan.json \
      --base-url http://engine:6767 >/dev/null 2>&1; then
      log "verified import journal and target counts for $completed records"
      return
    fi
    sleep 2
  done
  fail 'imported memory counts did not settle before the verification deadline'
}

legacy_compose() {
  docker compose -p "${RAM0_LEGACY_PROJECT:-ram0}" \
    --env-file "$RAM0_LEGACY_COMPOSE_DIR/.env" \
    --env-file "$RAM0_LEGACY_STATE_FILE" \
    -f "$RAM0_LEGACY_COMPOSE_DIR/docker-compose.yaml" \
    -f "$RAM0_LEGACY_COMPOSE_DIR/docker-compose.unraid.yaml" "$@"
}

promote() {
  candidate_compose stop graph gateway engine
  MUTATION_STARTED=true
  docker stop ram0_dashboard ram0_api >/dev/null
  live_compose up -d --force-recreate engine gateway graph
  "$SCRIPT_DIR/verify-stack.sh" \
    "http://$RAM0_HOST_IP:18888" "http://$RAM0_HOST_IP:13000" "$RAM0_RUNTIME_ENV_FILE" \
    "${RAM0_PUBLIC_API_URL:-}" "${RAM0_PUBLIC_GRAPH_URL:-}"
  MUTATION_STARTED=false
  log "deployed revision $RAM0_REVISION; legacy data remains untouched"
}

rollback() {
  ROLLBACK_RUNNING=true
  log 'rolling back application containers; no volumes will be removed'
  live_compose stop graph gateway engine >/dev/null 2>&1 || true
  legacy_compose up -d --no-build postgres mem0 mem0-dashboard
  ROLLBACK_RUNNING=false
  log "rollback complete; backup remains at $BACKUP_DIR"
}

on_error() {
  local status=$1
  trap - ERR
  if [[ $MUTATION_STARTED == true && $ROLLBACK_RUNNING == false ]]; then rollback || true; fi
  exit "$status"
}

self_test() {
  validate_sha 0123456789abcdef0123456789abcdef01234567
  ! validate_sha short
  validate_digest_ref repo/image@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  ! validate_digest_ref repo/image:latest
  printf 'deployment helper self-test passed\n'
}

main() {
  if [[ ${1:-} == --self-test ]]; then self_test; return; fi
  [[ $# -eq 0 ]] || fail 'usage: deploy-unraid.sh (configuration comes from RAM0_DEPLOY_ENV)'
  load_environment
  acquire_lock
  trap release_lock EXIT
  trap 'on_error $?' ERR
  preflight
  backup_legacy
  start_candidate
  import_memories
  promote
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
