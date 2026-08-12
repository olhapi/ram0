#!/usr/bin/env bash

set -euo pipefail

PROJECT_NAME="ram0-categories-verify"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${SERVER_DIR}/.." && pwd)"
COMPOSE_FILE="${SERVER_DIR}/docker-compose.categories-test.yaml"
TMP_BASE="${TMPDIR:-/tmp}"
TMP_BASE="${TMP_BASE%/}"

RAM0_API_PORT="${RAM0_API_PORT:-18888}"
RAM0_DASHBOARD_PORT="${RAM0_DASHBOARD_PORT:-13000}"
export RAM0_API_PORT RAM0_DASHBOARD_PORT

API_URL="http://127.0.0.1:${RAM0_API_PORT}"
DASHBOARD_URL="http://127.0.0.1:${RAM0_DASHBOARD_PORT}"
COMPOSE=(docker compose -p "${PROJECT_NAME}" -f "${COMPOSE_FILE}")
DEFAULT_CATALOG='[{"name":"personal_details","description":"Identity, age, location, education, and personal background."},{"name":"family","description":"Family members, relationships, household, and family events."},{"name":"professional_details","description":"Employment, career, workplace, skills, and professional goals."},{"name":"sports","description":"Sports played, followed, watched, or preferred."},{"name":"travel","description":"Trips, destinations, travel plans, and travel preferences."},{"name":"food","description":"Food, cooking, restaurants, diets, and dining preferences."},{"name":"music","description":"Artists, genres, instruments, concerts, and listening preferences."},{"name":"health","description":"Health conditions, care, wellness, fitness, and medical information."},{"name":"technology","description":"Devices, software, technical interests, and technology preferences."},{"name":"hobbies","description":"Leisure activities, crafts, collections, and recurring interests."},{"name":"fashion","description":"Clothing, style, accessories, sizes, and fashion preferences."},{"name":"entertainment","description":"Films, television, books, games, and other media preferences."},{"name":"milestones","description":"Important achievements, anniversaries, transitions, and life events."},{"name":"user_preferences","description":"General likes, dislikes, habits, choices, and preferred behavior."},{"name":"misc","description":"Useful personal context that does not fit another active category."}]'
PROJECT_CATALOG='[{"name":"billing","description":"Invoices and payments."},{"name":"travel","description":"Trips and destinations."}]'
PROJECT_WITH_SUPPORT='[{"name":"billing","description":"Invoices and payments."},{"name":"travel","description":"Trips and destinations."},{"name":"support","description":"Customer support."}]'
PROJECT_WITH_RENAMED_SUPPORT='[{"name":"billing","description":"Invoices and payments."},{"name":"travel","description":"Trips and destinations."},{"name":"customer_support","description":"Customer support cases."}]'
TEST_USER="ram0-category-acceptance"
SECRET_SENTINEL="MEMORY_SECRET_should_not_log_8e2d7c"
MALFORMED_SENTINEL="{invalid-json"
POLL_TIMEOUT_SECONDS="${RAM0_CATEGORY_VERIFY_TIMEOUT:-45}"
LOCK_DIR="/tmp/${PROJECT_NAME}.lock"
LOCK_OWNER=""
LOCK_HELD=false
STACK_TOUCHED=false
TMP_DIR=""
START_SECONDS="${SECONDS}"

pass() {
  printf 'PASS: %s\n' "$1"
}

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  return 1
}

compose_ps_ids() {
  "${COMPOSE[@]}" ps -aq
}

project_volume_ids() {
  docker volume ls -q --filter "label=com.docker.compose.project=${PROJECT_NAME}"
}

project_network_ids() {
  docker network ls -q --filter "label=com.docker.compose.project=${PROJECT_NAME}"
}

acquire_lock() {
  [[ -d "${TMP_BASE}" ]] || fail "Temporary base directory does not exist: ${TMP_BASE}"
  if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
    fail "Another ${PROJECT_NAME} verification owns ${LOCK_DIR}; refusing all Compose operations"
  fi
  LOCK_OWNER="pid=$$ token=${RANDOM}${RANDOM}"
  printf '%s\n' "${LOCK_OWNER}" > "${LOCK_DIR}/owner"
  LOCK_HELD=true
  pass "acquired exclusive project lock ${LOCK_DIR}"
}

release_own_lock() {
  local recorded_owner=""
  if [[ "${LOCK_HELD}" != true ]]; then
    return 0
  fi
  if [[ -f "${LOCK_DIR}/owner" ]]; then
    IFS= read -r recorded_owner < "${LOCK_DIR}/owner" || true
  fi
  if [[ "${recorded_owner}" != "${LOCK_OWNER}" ]]; then
    printf 'FAIL: lock ownership changed; preserving %s for manual inspection\n' "${LOCK_DIR}" >&2
    return 1
  fi
  rm -- "${LOCK_DIR}/owner"
  rmdir "${LOCK_DIR}"
  LOCK_HELD=false
}

preflight() {
  local existing
  local existing_networks
  local existing_volumes
  existing="$(compose_ps_ids)"
  if [[ -n "${existing}" ]]; then
    fail "Compose project ${PROJECT_NAME} already has containers; refusing to touch it"
  fi
  existing_volumes="$(project_volume_ids)"
  if [[ -n "${existing_volumes}" ]]; then
    fail "Compose project ${PROJECT_NAME} already has volumes; refusing to touch it"
  fi
  existing_networks="$(project_network_ids)"
  if [[ -n "${existing_networks}" ]]; then
    fail "Compose project ${PROJECT_NAME} already has networks; refusing to touch it"
  fi

  python3 - \
    "${RAM0_API_PORT}" \
    "${RAM0_DASHBOARD_PORT}" <<'PY'
import socket
import sys

ports = [int(value) for value in sys.argv[1:]]
if len(set(ports)) != len(ports):
    raise SystemExit(f"Chosen host ports must be unique: {ports}")

listeners = []
try:
    for port in ports:
        if not 1 <= port <= 65535:
            raise SystemExit(f"Invalid host port: {port}")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            sock.bind(("127.0.0.1", port))
            sock.listen(1)
        except OSError as error:
            sock.close()
            raise SystemExit(f"Host port {port} is occupied: {error}") from error
        listeners.append(sock)
finally:
    for listener in listeners:
        listener.close()
PY
  pass "preflight found no project containers, volumes, or networks and two unused loopback host ports"
}

cleanup() {
  local status=$?
  local cleanup_status=0
  local remaining=""
  local lock_status=0
  local owned_lock_at_entry="${LOCK_HELD}"
  trap - EXIT INT TERM
  set +e
  if [[ "${STACK_TOUCHED}" == true && "${LOCK_HELD}" == true ]]; then
    "${COMPOSE[@]}" down --volumes --remove-orphans
    cleanup_status=$?
    remaining="$(compose_ps_ids 2>/dev/null)"
  fi
  if [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]]; then
    case "${TMP_DIR}" in
      "${TMP_BASE}/${PROJECT_NAME}."*) find "${TMP_DIR}" -depth -delete ;;
      *)
        printf 'FAIL: refusing unexpected temporary path cleanup: %s\n' "${TMP_DIR}" >&2
        cleanup_status=1
        ;;
    esac
  fi
  if [[ ${cleanup_status} -eq 0 && -z "${remaining}" ]]; then
    release_own_lock
    lock_status=$?
  fi
  if [[ ${cleanup_status} -ne 0 || -n "${remaining}" || ${lock_status} -ne 0 ]]; then
    printf 'FAIL: cleanup did not empty project %s (compose_status=%s remaining=%s)\n' \
      "${PROJECT_NAME}" "${cleanup_status}" "${remaining:-none}" >&2
    status=1
  elif [[ "${STACK_TOUCHED}" == true ]]; then
    printf 'PASS: cleanup removed project %s containers, volumes, orphans, and owned lock\n' "${PROJECT_NAME}"
  elif [[ "${owned_lock_at_entry}" == true ]]; then
    printf 'PASS: released owned project lock without touching Compose\n'
  fi
  exit "${status}"
}

safe_json_diagnostic() {
  local file=$1
  python3 - "${file}" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, UnicodeError, json.JSONDecodeError):
    print('{"response":"unavailable_or_unparseable"}')
    raise SystemExit(0)

memory_fields = ("id", "category_status", "categories")
job_fields = ("id", "memory_id", "state", "attempts", "error_code")

if isinstance(data, dict) and any(key in data for key in memory_fields):
    safe = {key: data.get(key) for key in memory_fields}
elif isinstance(data, list):
    safe = [
        {key: item.get(key) for key in job_fields}
        for item in data
        if isinstance(item, dict)
    ]
else:
    safe = {"response_type": type(data).__name__}
print(json.dumps(safe, separators=(",", ":"), sort_keys=True))
PY
}

print_timeout_failure() {
  local description=$1
  local timeout_seconds=$2
  local observed_response=$3
  local output=$4
  if [[ "${observed_response}" == true ]]; then
    printf 'FAIL: %s after %ss; last safe state: %s\n' \
      "${description}" "${timeout_seconds}" "$(safe_json_diagnostic "${output}")" >&2
  else
    printf 'FAIL: %s after %ss; last safe state: {"response":"unavailable"}\n' \
      "${description}" "${timeout_seconds}" >&2
  fi
  return 1
}

json_assert() {
  local file=$1
  local description=$2
  local expression=$3
  python3 - "${file}" "${description}" "${expression}" <<'PY'
import json
import sys

path, description, expression = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
scope = {
    "data": data,
    "len": len,
    "set": set,
    "sum": sum,
    "all": all,
    "any": any,
    "next": next,
    "sorted": sorted,
    "isinstance": isinstance,
    "list": list,
    "dict": dict,
    "str": str,
    "int": int,
}
try:
    result = eval(expression, {"__builtins__": {}, **scope}, {})
except Exception as error:
    print(f"FAIL: {description}: assertion error: {error}; JSON={data!r}", file=sys.stderr)
    raise SystemExit(1) from error
if not result:
    print(f"FAIL: {description}; JSON={data!r}", file=sys.stderr)
    raise SystemExit(1)
print(f"PASS: {description}")
PY
}

json_matches() {
  local file=$1
  local expression=$2
  python3 - "${file}" "${expression}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
scope = {
    "data": data,
    "len": len,
    "set": set,
    "sum": sum,
    "all": all,
    "any": any,
    "sorted": sorted,
    "isinstance": isinstance,
    "list": list,
    "dict": dict,
    "str": str,
    "int": int,
}
raise SystemExit(0 if eval(sys.argv[2], {"__builtins__": {}, **scope}, {}) else 1)
PY
}

json_value() {
  local file=$1
  local expression=$2
  python3 - "${file}" "${expression}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
value = eval(
    sys.argv[2],
    {
        "__builtins__": {},
        "data": data,
        "len": len,
        "sum": sum,
        "next": next,
        "str": str,
    },
    {},
)
if isinstance(value, (dict, list)):
    print(json.dumps(value, separators=(",", ":")))
elif value is None:
    print("null")
else:
    print(value)
PY
}

request() {
  local method=$1
  local base_url=$2
  local path=$3
  local output=$4
  local body=${5-}
  local args=(
    --silent
    --show-error
    --fail-with-body
    --connect-timeout 5
    --max-time 30
    --request "${method}"
    --header "Content-Type: application/json"
    --output "${output}"
  )
  if [[ -n "${body}" ]]; then
    args+=(--data-binary "${body}")
  fi
  curl "${args[@]}" "${base_url}${path}"
}

api_request() {
  request "$1" "${API_URL}" "$2" "$3" "${4-}"
}

dashboard_request() {
  request "$1" "${DASHBOARD_URL}" "$2" "$3" "${4-}"
}

wait_for_json() {
  local path=$1
  local expression=$2
  local description=$3
  local output=$4
  local deadline=$((SECONDS + POLL_TIMEOUT_SECONDS))
  local observed_response=false
  while (( SECONDS < deadline )); do
    if api_request GET "${path}" "${output}" 2>/dev/null; then
      observed_response=true
      if json_matches "${output}" "${expression}"; then
        pass "${description}"
        return 0
      fi
    fi
    sleep 1
  done
  print_timeout_failure "${description}" "${POLL_TIMEOUT_SECONDS}" "${observed_response}" "${output}"
}

wait_for_memory() {
  local memory_id=$1
  local expression=$2
  local description=$3
  wait_for_json "/memories/${memory_id}" "${expression}" "${description}" "${TMP_DIR}/memory-${memory_id}.json"
}

wait_for_job() {
  local memory_id=$1
  local expression=$2
  local description=$3
  wait_for_json "/categories/jobs?limit=100" \
    "any(job['memory_id'] == '${memory_id}' and (${expression}) for job in data)" \
    "${description}" \
    "${TMP_DIR}/jobs-${memory_id}.json"
}

add_memory() {
  local output=$1
  local text=$2
  local user_id=$3
  local custom_categories=${4-}
  local body
  body="$(python3 - "${text}" "${user_id}" "${custom_categories}" <<'PY'
import json
import sys

body = {
    "messages": [{"role": "user", "content": sys.argv[1]}],
    "user_id": sys.argv[2],
    "infer": False,
}
if sys.argv[3]:
    body["custom_categories"] = json.loads(sys.argv[3])
print(json.dumps(body, separators=(",", ":")))
PY
)"
  api_request POST /memories "${output}" "${body}"
  json_assert "${output}" "add returns one pending memory" \
    "len(data.get('results', [])) == 1 and data['results'][0].get('category_status') == 'pending' and data['results'][0].get('categories') is None"
}

memory_id_from_add() {
  json_value "$1" "data['results'][0]['id']"
}

get_jobs() {
  api_request GET "/categories/jobs?limit=100" "$1"
}

job_count_for() {
  local memory_id=$1
  local output="${TMP_DIR}/jobs-count-${memory_id}.json"
  get_jobs "${output}"
  json_value "${output}" "sum(1 for job in data if job['memory_id'] == '${memory_id}')"
}

active_job_count_for_db() {
  local memory_id=$1
  "${COMPOSE[@]}" exec -T postgres psql -U postgres -d mem0_app -tAc \
    "SELECT count(*) FROM category_jobs WHERE memory_id = '${memory_id}' AND state IN ('queued','processing','retrying');" | tr -d '[:space:]'
}

psql_app() {
  "${COMPOSE[@]}" exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d mem0_app -tAc "$1"
}

psql_memories() {
  "${COMPOSE[@]}" exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d postgres -tAc "$1"
}

recreate_api() {
  local worker_enabled=$1
  if [[ -n "${TMP_DIR}" ]] && [[ -n "$("${COMPOSE[@]}" ps -q ram0-api 2>/dev/null)" ]]; then
    "${COMPOSE[@]}" logs --no-color ram0-api openai-stub >> "${TMP_DIR}/focused.log"
  fi
  CATEGORY_WORKER_ENABLED="${worker_enabled}" "${COMPOSE[@]}" up -d --wait --no-deps --force-recreate ram0-api
  pass "API recreated with CATEGORY_WORKER_ENABLED=${worker_enabled}"
}

wait_for_all_jobs_terminal() {
  local output="${TMP_DIR}/jobs-terminal.json"
  wait_for_json "/categories/jobs?limit=100" \
    "all(job['state'] not in {'queued','processing','retrying'} for job in data)" \
    "all category jobs reached terminal states" \
    "${output}"
}

redaction_self_test() {
  local test_dir
  local test_file
  local diagnostic
  local timeout_output
  test_dir="$(mktemp -d "${TMP_BASE}/${PROJECT_NAME}.redaction.XXXXXX")"
  test_file="${test_dir}/unsafe.json"
  printf '%s\n' \
    "{\"id\":\"memory-safe-id\",\"category_status\":\"pending\",\"categories\":null,\"memory\":\"${SECRET_SENTINEL}\",\"metadata\":{\"raw\":\"${MALFORMED_SENTINEL}\"}}" \
    > "${test_file}"
  diagnostic="$(safe_json_diagnostic "${test_file}")"
  if [[ "${diagnostic}" == *"${SECRET_SENTINEL}"* || "${diagnostic}" == *"${MALFORMED_SENTINEL}"* ]]; then
    fail "safe timeout diagnostic leaked disallowed content"
  fi
  [[ "${diagnostic}" == *'"id":"memory-safe-id"'* ]] || fail "safe timeout diagnostic omitted the memory ID"
  if timeout_output="$(print_timeout_failure "forced timeout" 0 true "${test_file}" 2>&1)"; then
    fail "forced timeout diagnostic unexpectedly succeeded"
  fi
  if [[ "${timeout_output}" == *"${SECRET_SENTINEL}"* || "${timeout_output}" == *"${MALFORMED_SENTINEL}"* ]]; then
    fail "forced timeout failure leaked disallowed content"
  fi
  [[ "${timeout_output}" == *'"id":"memory-safe-id"'* ]] || fail "forced timeout failure omitted safe state"
  rm -- "${test_file}"
  rmdir "${test_dir}"
  pass "timeout diagnostics expose only allowlisted state"
}

main() {
  cd "${REPO_ROOT}"
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  acquire_lock
  preflight
  TMP_DIR="$(mktemp -d "${TMP_BASE}/${PROJECT_NAME}.run.XXXXXX")"
  STACK_TOUCHED=true

  printf 'Building isolated category acceptance images...\n'
  "${COMPOSE[@]}" build ram0-api openai-stub ram0-dashboard
  "${COMPOSE[@]}" up -d --wait postgres
  pass "PostgreSQL started in isolated project"

"${COMPOSE[@]}" run --rm --no-deps ram0-api alembic upgrade head
[[ "$(psql_app "SELECT version_num FROM alembic_version;")" == "007" ]] || fail "expected Alembic revision 007"
"${COMPOSE[@]}" run --rm --no-deps ram0-api alembic downgrade 006
[[ "$(psql_app "SELECT version_num FROM alembic_version;")" == "006" ]] || fail "expected Alembic revision 006"
[[ "$(psql_app "SELECT to_regclass('public.category_jobs') IS NULL;")" == "t" ]] || fail "category_jobs survived downgrade to 006"
"${COMPOSE[@]}" run --rm --no-deps ram0-api alembic upgrade 007
[[ "$(psql_app "SELECT version_num FROM alembic_version;")" == "007" ]] || fail "expected re-upgrade to revision 007"
[[ "$(psql_app "SELECT to_regclass('public.category_jobs') = 'category_jobs'::regclass;")" == "t" ]] || fail "category_jobs missing after upgrade"
[[ "$(psql_app "SELECT to_regclass('public.uq_category_jobs_active_memory') IS NOT NULL;")" == "t" ]] || fail "active-memory unique index missing after upgrade"
pass "migration 007 upgrades, downgrades to 006, and restores its table and active-job index"

"${COMPOSE[@]}" up -d --wait openai-stub ram0-api ram0-dashboard
pass "API, deterministic provider stub, and dashboard are healthy"

catalog_json="${TMP_DIR}/catalog.json"
api_request GET /categories "${catalog_json}"
json_assert "${catalog_json}" "default catalog has exact documented order" \
  "data['source'] == 'defaults' and data['active'] == ${DEFAULT_CATALOG} and data['saved'] == []"

api_request PUT /categories "${catalog_json}" "${PROJECT_CATALOG}"
json_assert "${catalog_json}" "user catalog replaces defaults in supplied order" \
  "data['source'] == 'user' and data['saved'] == ${PROJECT_CATALOG} and data['active'] == ${PROJECT_CATALOG}"
api_request POST /categories "${catalog_json}" \
  '{"name":"support","description":"Customer support."}'
json_assert "${catalog_json}" "catalog POST appends the new definition in order" \
  "data['source'] == 'user' and data['saved'] == ${PROJECT_WITH_SUPPORT} and data['active'] == ${PROJECT_WITH_SUPPORT}"
api_request PATCH "/categories/support" "${catalog_json}" \
  '{"name":"customer_support","description":"Customer support cases."}'
json_assert "${catalog_json}" "catalog PATCH preserves order and changes name and description" \
  "data['source'] == 'user' and data['saved'] == ${PROJECT_WITH_RENAMED_SUPPORT} and data['active'] == ${PROJECT_WITH_RENAMED_SUPPORT}"
api_request DELETE "/categories/customer_support" "${catalog_json}"
json_assert "${catalog_json}" "catalog DELETE restores the expected user catalog" \
  "data['source'] == 'user' and data['saved'] == ${PROJECT_CATALOG} and data['active'] == ${PROJECT_CATALOG}"
api_request PUT /categories "${catalog_json}" '[]'
json_assert "${catalog_json}" "empty saved catalog resets to defaults" \
  "data['source'] == 'defaults' and data['saved'] == [] and data['active'] == ${DEFAULT_CATALOG}"
api_request PUT /categories "${catalog_json}" "${PROJECT_CATALOG}"

override_add="${TMP_DIR}/override-add.json"
add_memory "${override_add}" "Per-call override memory" "${TEST_USER}" '[{"override":"One-call-only label."}]'
override_id="$(memory_id_from_add "${override_add}")"
wait_for_memory "${override_id}" \
  "data.get('category_status') == 'completed' and data.get('categories') == ['override']" \
  "per-call override classifies with its request-only catalog"
api_request GET /categories "${catalog_json}"
json_assert "${catalog_json}" "per-call override does not persist" \
  "data['source'] == 'user' and data['saved'] == ${PROJECT_CATALOG} and data['active'] == ${PROJECT_CATALOG}"

single_add="${TMP_DIR}/single-add.json"
add_memory "${single_add}" "invoice ready for payment" "${TEST_USER}"
single_id="$(memory_id_from_add "${single_add}")"
wait_for_memory "${single_id}" \
  "data.get('category_status') == 'completed' and data.get('categories') == ['billing']" \
  "single-label classification completes"

multi_add="${TMP_DIR}/multi-add.json"
add_memory "${multi_add}" "__CATEGORY_MULTI__ two matching labels" "${TEST_USER}"
multi_id="$(memory_id_from_add "${multi_add}")"
wait_for_memory "${multi_id}" \
  "data.get('category_status') == 'completed' and data.get('categories') == ['billing','travel']" \
  "multi-label classification completes in catalog order"

none_add="${TMP_DIR}/none-add.json"
add_memory "${none_add}" "__CATEGORY_NONE__ no matching labels" "${TEST_USER}"
none_id="$(memory_id_from_add "${none_add}")"
wait_for_memory "${none_id}" \
  "data.get('category_status') == 'completed' and data.get('categories') == []" \
  "zero-label classification completes"

unknown_add="${TMP_DIR}/unknown-add.json"
add_memory "${unknown_add}" "__CATEGORY_UNKNOWN__ discard invented labels" "${TEST_USER}"
unknown_id="$(memory_id_from_add "${unknown_add}")"
wait_for_memory "${unknown_id}" \
  "data.get('category_status') == 'completed' and data.get('categories') == []" \
  "unknown provider labels are discarded"

malformed_add="${TMP_DIR}/malformed-add.json"
add_memory "${malformed_add}" \
  "__CATEGORY_MALFORMED__ ${SECRET_SENTINEL} invoice after two malformed responses" \
  "${TEST_USER}"
malformed_id="$(memory_id_from_add "${malformed_add}")"
wait_for_job "${malformed_id}" \
  "job['state'] in {'completed','failed'} and job['attempts'] == 3" \
  "malformed classifier output reaches a terminal third attempt"
malformed_job_id="$(json_value "${TMP_DIR}/jobs-${malformed_id}.json" \
  "next(job['id'] for job in data if job['memory_id'] == '${malformed_id}')")"
wait_for_memory "${malformed_id}" \
  "(data.get('category_status') == 'completed' and data.get('categories') == ['billing']) or (data.get('category_status') == 'failed' and data.get('categories') == [])" \
  "malformed output eventually succeeds or exposes terminal failure"

metadata_jobs_before="$(job_count_for "${single_id}")"
api_request PUT "/memories/${single_id}" "${TMP_DIR}/metadata-update.json" '{"metadata":{"acceptance":"metadata-only"}}'
sleep 2
metadata_jobs_after="$(job_count_for "${single_id}")"
[[ "${metadata_jobs_before}" == "${metadata_jobs_after}" ]] || fail "metadata-only update queued category work"
wait_for_memory "${single_id}" \
  "data.get('category_status') == 'completed' and data.get('categories') == ['billing'] and data.get('metadata', {}).get('acceptance') == 'metadata-only'" \
  "metadata-only update preserves classification without a new job"

api_request PUT "/memories/${single_id}" "${TMP_DIR}/text-update.json" '{"text":"__CATEGORY_NONE__ updated text"}'
wait_for_memory "${single_id}" \
  "data.get('category_status') == 'completed' and data.get('categories') == [] and data.get('memory') == '__CATEGORY_NONE__ updated text'" \
  "text update queues and completes reclassification"
text_jobs_after="$(job_count_for "${single_id}")"
[[ "${text_jobs_after}" -eq $((metadata_jobs_after + 1)) ]] || fail "text update did not create exactly one replacement job"
pass "text update created one new job while metadata update created none"

filter_list="${TMP_DIR}/filter-list.json"
api_request GET "/memories?user_id=${TEST_USER}&categories=billing&categories=travel&top_k=100" "${filter_list}"
json_assert "${filter_list}" "repeated list category filters use ANY semantics" \
  "'${multi_id}' in {item['id'] for item in data['results']} and '${malformed_id}' in {item['id'] for item in data['results']}"
filter_search="${TMP_DIR}/filter-search.json"
api_request POST /search "${filter_search}" \
  "{\"query\":\"invoice travel\",\"filters\":{\"user_id\":\"${TEST_USER}\",\"categories\":{\"in\":[\"billing\",\"travel\"]}},\"top_k\":100}"
json_assert "${filter_search}" "nested search category filters use ANY semantics" \
  "any(item['id'] == '${malformed_id}' and item['categories'] == ['billing'] for item in data['results'])"

legacy_add="${TMP_DIR}/legacy-add.json"
add_memory "${legacy_add}" "invoice legacy payload probe" "${TEST_USER}"
legacy_id="$(memory_id_from_add "${legacy_add}")"
wait_for_memory "${legacy_id}" \
  "data.get('category_status') == 'completed' and data.get('categories') == ['billing']" \
  "legacy probe initially classifies"
psql_memories \
  "UPDATE memories SET payload = payload - 'categories' - 'category_status' WHERE id = '${legacy_id}'::uuid;" \
  >/dev/null
[[ "$(psql_memories "SELECT count(*) FROM memories WHERE id = '${legacy_id}'::uuid AND NOT (payload ? 'categories') AND NOT (payload ? 'category_status');" | tr -d '[:space:]')" == "1" ]] \
  || fail "targeted legacy payload update did not remove both fields from exactly the known row"
api_request GET "/memories/${legacy_id}" "${TMP_DIR}/legacy-get.json"
json_assert "${TMP_DIR}/legacy-get.json" "legacy payload exposes top-level unclassified state" \
  "data.get('id') == '${legacy_id}' and data.get('categories') is None and data.get('category_status') == 'unclassified'"

recreate_api false
jobs_before_preview="$(psql_app "SELECT count(*) FROM category_jobs;" | tr -d '[:space:]')"
api_request POST /categories/reclassify/preview "${TMP_DIR}/preview.json" \
  '{"scope":"unclassified_failed","input_rate_per_million":1.5,"output_rate_per_million":2.5}'
json_assert "${TMP_DIR}/preview.json" "preview estimates eligible memories without model work" \
  "data['eligible_memories'] >= 1 and data['estimated_calls'] == data['eligible_memories'] and data['estimated_input_tokens'] > 0 and data['estimated_output_tokens'] > 0 and data['estimated_cost'] is not None"
jobs_after_preview="$(psql_app "SELECT count(*) FROM category_jobs;" | tr -d '[:space:]')"
[[ "${jobs_before_preview}" == "${jobs_after_preview}" ]] || fail "preview created category jobs"
api_request POST /categories/reclassify "${TMP_DIR}/reclassify-first.json" \
  '{"scope":"unclassified_failed","confirm":"RECLASSIFY"}'
json_assert "${TMP_DIR}/reclassify-first.json" "confirmed reclassification creates eligible jobs" \
  "data['created_jobs'] == data['eligible_memories'] and data['created_jobs'] >= 1 and data['skipped_active_jobs'] == 0"
reclass_created="$(json_value "${TMP_DIR}/reclassify-first.json" "data['created_jobs']")"
api_request POST /categories/reclassify "${TMP_DIR}/reclassify-second.json" \
  '{"scope":"unclassified_failed","confirm":"RECLASSIFY"}'
json_assert "${TMP_DIR}/reclassify-second.json" "confirmed reclassification is idempotent while jobs are active" \
  "data['created_jobs'] == 0"
jobs_after_execute="$(psql_app "SELECT count(*) FROM category_jobs;" | tr -d '[:space:]')"
[[ "${jobs_after_execute}" -eq $((jobs_after_preview + reclass_created)) ]] || fail "confirmed execution created duplicate jobs"
pass "preview created no jobs and confirmed execution created no duplicate active jobs"
recreate_api true
wait_for_all_jobs_terminal

recreate_api false
delete_add="${TMP_DIR}/delete-add.json"
add_memory "${delete_add}" "invoice queued before delete" "${TEST_USER}"
delete_id="$(memory_id_from_add "${delete_add}")"
[[ "$(active_job_count_for_db "${delete_id}")" == "1" ]] || fail "delete probe did not have one active job"
api_request DELETE "/memories/${delete_id}" "${TMP_DIR}/delete.json"
wait_for_job "${delete_id}" \
  "job['state'] == 'cancelled' and job['error_code'] == 'memory_deleted'" \
  "delete cancels the pending category job"
recreate_api true
sleep 2
[[ "$(psql_memories "SELECT count(*) FROM memories WHERE id = '${delete_id}'::uuid;" | tr -d '[:space:]')" == "0" ]] \
  || fail "deleted memory reappeared after worker restart"
[[ "$(active_job_count_for_db "${delete_id}")" == "0" ]] || fail "deleted memory retained active category work"
pass "delete prevents a late worker write"

recreate_api false
restart_add="${TMP_DIR}/restart-add.json"
add_memory "${restart_add}" "invoice durable restart recovery" "${TEST_USER}"
restart_id="$(memory_id_from_add "${restart_add}")"
get_jobs "${TMP_DIR}/restart-jobs-before.json"
json_assert "${TMP_DIR}/restart-jobs-before.json" "worker-disabled add has one untouched queued job" \
  "len([job for job in data if job['memory_id'] == '${restart_id}']) == 1 and next(job for job in data if job['memory_id'] == '${restart_id}')['state'] == 'queued' and next(job for job in data if job['memory_id'] == '${restart_id}')['attempts'] == 0"
restart_job_id="$(json_value "${TMP_DIR}/restart-jobs-before.json" "next(job['id'] for job in data if job['memory_id'] == '${restart_id}')")"
recreate_api true
wait_for_job "${restart_id}" \
  "job['id'] == '${restart_job_id}' and job['state'] == 'completed' and job['attempts'] == 1" \
  "restart recovers the same durable job exactly once"
wait_for_memory "${restart_id}" \
  "data.get('category_status') == 'completed' and data.get('categories') == ['billing']" \
  "restart recovery writes the classified payload"
[[ "$(psql_app "SELECT count(*) FROM category_jobs WHERE memory_id = '${restart_id}';" | tr -d '[:space:]')" == "1" ]] \
  || fail "restart recovery created a duplicate job"
[[ "$(active_job_count_for_db "${restart_id}")" == "0" ]] || fail "restart recovery left an active job"
pass "restart recovery preserved job ${restart_job_id} with attempts=1 and no duplicate active job"

dashboard_health_code="$(curl --silent --show-error --fail-with-body --connect-timeout 5 --max-time 30 \
  --output "${TMP_DIR}/dashboard-health.json" --write-out '%{http_code}' "${DASHBOARD_URL}/api/health")"
[[ "${dashboard_health_code}" == "200" ]] || fail "dashboard health returned HTTP ${dashboard_health_code}, expected 200"
json_assert "${TMP_DIR}/dashboard-health.json" "dashboard health route returns status ok" \
  "data.get('status') == 'ok'"
dashboard_categories_code="$(curl --silent --show-error --fail-with-body --connect-timeout 5 --max-time 30 \
  --cookie 'mem0_refresh_token=ram0-acceptance' \
  --output "${TMP_DIR}/dashboard-categories.html" --write-out '%{http_code}' \
  "${DASHBOARD_URL}/dashboard/categories")"
[[ "${dashboard_categories_code}" == "200" ]] || fail "dashboard categories returned HTTP ${dashboard_categories_code}, expected 200"
[[ -s "${TMP_DIR}/dashboard-categories.html" ]] || fail "dashboard categories route returned an empty body"
pass "dashboard categories route returns HTTP 200 with content"

"${COMPOSE[@]}" logs --no-color ram0-api openai-stub >> "${TMP_DIR}/focused.log"
if grep -Fq "${SECRET_SENTINEL}" "${TMP_DIR}/focused.log"; then
  fail "prompt-injection secret sentinel appeared in focused logs"
fi
if grep -Fq "${MALFORMED_SENTINEL}" "${TMP_DIR}/focused.log"; then
  fail "raw malformed provider body appeared in focused logs"
fi
python3 - "${TMP_DIR}/focused.log" "${malformed_job_id}" "${malformed_id}" <<'PY'
import sys

path, job_id, memory_id = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    correlated = any(
        "category_worker_job_" in line
        and f"job_id={job_id}" in line
        and f"memory_id={memory_id}" in line
        for line in handle
    )
if not correlated:
    raise SystemExit(
        f"FAIL: no retry/terminal log line correlates exact job_id={job_id} and memory_id={memory_id}"
    )
PY
pass "focused logs redact secret/raw provider bodies and retain correlated job and memory IDs"

printf 'PASS: all category container assertions completed in %ss\n' "$((SECONDS - START_SECONDS))"
}

lock_self_test() {
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  acquire_lock
  sleep "${RAM0_CATEGORY_VERIFY_LOCK_HOLD_SECONDS:-2}"
}

case "${RAM0_CATEGORY_VERIFY_SELF_TEST:-}" in
  "") main ;;
  lock) lock_self_test ;;
  redaction) redaction_self_test ;;
  *) fail "Unknown RAM0_CATEGORY_VERIFY_SELF_TEST mode" ;;
esac
