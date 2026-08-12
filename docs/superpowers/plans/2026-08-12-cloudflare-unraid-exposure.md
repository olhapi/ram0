# Cloudflare Exposure for Ram0 on Unraid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Configure the deployed Ram0 instance to use `brain-api.olhapi.com` and `brain.olhapi.com` as its canonical public origins while retaining its existing Unraid LAN bindings.

**Architecture:** Keep Ram0 API and dashboard published only on the existing Unraid IPv4 listeners at ports `18888` and `13000`. Update the root-only server environment atomically, validate the fully rendered Compose model with current immutable deployment state, then apply it through the guarded deployment script. Cloudflared remains independently managed and will target the verified LAN listeners over HTTP.

**Tech Stack:** Unraid, Docker Compose, Bash, FastAPI CORS configuration, Next.js runtime configuration, cloudflared

## Global Constraints

- API canonical public origin is exactly `https://brain-api.olhapi.com`.
- Dashboard canonical public origin is exactly `https://brain.olhapi.com`.
- Keep API `${RAM0_HOST_IP}:18888`, dashboard `${RAM0_HOST_IP}:13000`, and PostgreSQL internal-only.
- Do not print, copy, or rewrite unrelated secrets from `server/.env`.
- Do not configure Cloudflare Tunnel, DNS, Access, or WAF policy.
- Do not change authentication, disable origin validation, or add a shared Docker network.
- Preserve immutable image references and use the guarded Unraid deployment path.

---

### Task 1: Update and Verify the Unraid Deployment

**Files:**
- Modify on Unraid: `/mnt/user/appdata/mem0/repo/server/.env`
- Read on Unraid: `/mnt/user/appdata/mem0/deploy/current.env`
- Read on Unraid: `/mnt/user/appdata/mem0/repo/server/docker-compose.yaml`
- Read on Unraid: `/mnt/user/appdata/mem0/repo/server/docker-compose.unraid.yaml`
- Use on Unraid: `/mnt/user/appdata/mem0/repo/server/scripts/deploy_unraid.sh`

**Interfaces:**
- Consumes: the existing `RAM0_HOST_IP`, current immutable `RAM0_REVISION`, API image digest, dashboard image digest, and root-owned `server/.env`.
- Produces: a running Ram0 deployment whose API receives `DASHBOARD_URL=https://brain.olhapi.com`, whose dashboard receives `NEXT_PUBLIC_API_URL=https://brain-api.olhapi.com`, and whose verified LAN targets are suitable for cloudflared.

- [ ] **Step 1: Audit the live deployment without revealing secrets**

Run read-only commands over the configured Bitwarden SSH agent. Confirm the host, Compose project, container status, `.env` owner/mode, current revision, and only the three relevant environment-key presences/values. Do not output the whole environment file.

```bash
cd /mnt/user/appdata/mem0/repo
docker compose ls
docker ps --filter label=com.docker.compose.project=ram0 --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
stat -c '%U:%a %n' server/.env /mnt/user/appdata/mem0/deploy/current.env
awk -F= '$1 == "RAM0_REVISION" {print $1 "=" $2}' /mnt/user/appdata/mem0/deploy/current.env
awk -F= '$1 == "RAM0_HOST_IP" || $1 == "RAM0_PUBLIC_API_URL" || $1 == "RAM0_DASHBOARD_URL" || $1 == "DASHBOARD_URL" {print}' server/.env
```

Expected: `server/.env` is `root:600`; the `ram0` project is running; the API and dashboard use ports `18888` and `13000`; PostgreSQL has no published host port.

- [ ] **Step 2: Create a recoverable, root-only backup and atomically update only the origin keys**

Create a timestamped mode-`600` backup next to the environment file. Use a bounded Perl substitution to replace existing assignments, then verify every required key occurs exactly once. If a key is absent or duplicated, restore the backup and stop instead of appending ambiguously.

```bash
cd /mnt/user/appdata/mem0/repo/server
stamp=$(date -u +%Y%m%d-%H%M%S)
backup=".env.pre-cloudflare-$stamp"
install -m 600 -o root -g root .env "$backup"
for key in RAM0_PUBLIC_API_URL RAM0_DASHBOARD_URL DASHBOARD_URL; do
  test "$(awk -F= -v key="$key" '$1 == key {count++} END {print count+0}' .env)" -eq 1 || {
    install -m 600 -o root -g root "$backup" .env
    echo "Refusing ambiguous update for $key" >&2
    exit 1
  }
done
perl -0pi -e 's{^RAM0_PUBLIC_API_URL=.*$}{RAM0_PUBLIC_API_URL=https://brain-api.olhapi.com}m; s{^RAM0_DASHBOARD_URL=.*$}{RAM0_DASHBOARD_URL=https://brain.olhapi.com}m; s{^DASHBOARD_URL=.*$}{DASHBOARD_URL=https://brain.olhapi.com}m' .env
chown root:root .env
chmod 600 .env
```

Expected: only the three named assignments change; the backup remains root-only and recoverable.

- [ ] **Step 3: Render Compose and machine-check the exposure boundary**

Render with both environment inputs, then inspect only the relevant service fields. Validate the exact bindings, injected origins, and absence of a PostgreSQL published port before mutation.

```bash
cd /mnt/user/appdata/mem0/repo/server
rendered=$(mktemp /tmp/ram0-cloudflare-compose.XXXXXX.json)
docker compose -p ram0 \
  --env-file .env \
  --env-file /mnt/user/appdata/mem0/deploy/current.env \
  -f docker-compose.yaml \
  -f docker-compose.unraid.yaml \
  config --format json >"$rendered"
jq -e '
  .services.mem0.ports == [{"mode":"ingress","host_ip":"192.168.1.2","target":8000,"published":"18888","protocol":"tcp"}] and
  .services["mem0-dashboard"].ports == [{"mode":"ingress","host_ip":"192.168.1.2","target":3000,"published":"13000","protocol":"tcp"}] and
  (.services.postgres.ports // []) == [] and
  .services.mem0.environment.DASHBOARD_URL == "https://brain.olhapi.com" and
  .services["mem0-dashboard"].environment.NEXT_PUBLIC_API_URL == "https://brain-api.olhapi.com"
' "$rendered" >/dev/null
printf 'API target: http://192.168.1.2:18888\n'
printf 'Dashboard target: http://192.168.1.2:13000\n'
rm -f "$rendered"
```

Expected: assertions pass and the command prints the exact two LAN targets for Cloudflare configuration.

- [ ] **Step 4: Apply the environment change through the guarded deploy path**

Read the already-deployed full revision from root-only deployment state and pass it unchanged to the deployment script. This intentionally recreates the application services with the same immutable images while retaining backup and rollback checks.

```bash
cd /mnt/user/appdata/mem0/repo
revision=$(awk -F= '$1 == "RAM0_REVISION" {print $2; exit}' /mnt/user/appdata/mem0/deploy/current.env)
test "${#revision}" -eq 40
sudo server/scripts/deploy_unraid.sh "$revision"
```

Expected: the guarded command completes, promotes the same immutable revision, and reports successful direct and configured-origin health checks. If its public-origin checks fail because Cloudflare has not yet been configured, do not bypass the checks; restore the saved `.env` backup and report that Cloudflare must be configured before deployment can be promoted.

- [ ] **Step 5: Verify live configuration and direct LAN health**

Inspect the recreated containers and test only non-secret runtime properties.

```bash
cd /mnt/user/appdata/mem0/repo
host_ip=$(awk -F= '$1 == "RAM0_HOST_IP" {print $2; exit}' server/.env)
docker inspect ram0_api --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -Fx 'DASHBOARD_URL=https://brain.olhapi.com'
docker inspect ram0_dashboard --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -Fx 'NEXT_PUBLIC_API_URL=https://brain-api.olhapi.com'
curl --fail --silent --show-error --max-time 5 "http://$host_ip:18888/docs" >/dev/null
curl --fail --silent --show-error --max-time 5 "http://$host_ip:13000/api/health" >/dev/null
docker port ram0_postgres | test -z "$(cat)"
```

Expected: both environment checks and LAN health checks pass; PostgreSQL prints no published port.

- [ ] **Step 6: Record the Cloudflare handoff and post-configuration checks**

Give the user the exact values discovered in Step 3:

```text
brain-api.olhapi.com -> http://<verified RAM0_HOST_IP>:18888
brain.olhapi.com     -> http://<verified RAM0_HOST_IP>:13000
```

After the user configures Cloudflare, verify:

```bash
curl --fail --silent --show-error --max-time 10 https://brain-api.olhapi.com/docs >/dev/null
curl --fail --silent --show-error --max-time 10 https://brain.olhapi.com/api/health >/dev/null
curl --fail --silent --show-error --max-time 10 \
  -H 'Origin: https://brain.olhapi.com' \
  -D - -o /dev/null https://brain-api.olhapi.com/auth/setup-status
```

Expected: both HTTPS checks succeed and the API response permits exactly the configured dashboard origin. Perform dashboard login and one authenticated API request manually; do not put credentials in shell history or task output.

- [ ] **Step 7: Commit only repository documentation if it changed during execution**

Runtime changes live on Unraid and are not committed. If execution required a correction to this plan or the design, stage only those documentation files and use a Conventional Commit:

```bash
git add docs/superpowers/specs/2026-08-12-cloudflare-unraid-exposure-design.md docs/superpowers/plans/2026-08-12-cloudflare-unraid-exposure.md
git commit -m "docs: finalize Ram0 Cloudflare deployment"
```

Expected: unrelated pre-existing untracked files remain untouched.
