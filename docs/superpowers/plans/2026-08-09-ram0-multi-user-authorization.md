# Ram0 Multi-User Authorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each Ram0 account an isolated, account-derived memory namespace and add minimal copied-link account invitations with safe upgrade migration.

**Architecture:** A small `MemoryPrincipal` policy between FastAPI authentication and the existing Mem0 engine injects the authenticated account UUID as every memory operation's `user_id`. A startup migration claims legacy pgvector memories and category jobs for the sole administrator before multi-user invitations are enabled; separate admin routes and dashboard pages manage hashed, one-time invitations and reversible account disablement.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL/pgvector, Pydantic v2, pytest, Next.js 15, React 19, TypeScript 5.6, pnpm 10.

## Global Constraints

- The authenticated Ram0 account UUID is the canonical Mem0 `user_id`; callers never select or override it.
- `agent_id` and `run_id` remain optional organization fields inside an owner's namespace.
- Admin status grants no read, update, delete, reset, entity, category-count, category-job, or reclassification access to another account's memories.
- Missing and foreign memory IDs return the same `404 Memory not found` response.
- Invitations accept one email, expire after exactly seven days, use at least 256 bits of entropy, store only a SHA-256 token hash, and show the usable URL once.
- Public registration remains bootstrap-only; invited accounts always receive role `member` and initially use their email as display name.
- Existing pgvector memories and unowned category jobs automatically migrate to the sole administrator before invitations become available.
- Do not modify Mem0 core memory or vector-store modules, add runtime dependencies, modify CI workflows, expose secrets, or touch the untracked MCP plan before the final reconciliation task.
- Use `rtk` for shell commands, `pnpm` for dashboard commands, Ruff line length 120, and Conventional Commits.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `server/alembic/versions/008_multi_user_authorization.py` | Add account disablement, invitations, category-job owner, and indexes. |
| `server/models.py` | SQLAlchemy `UserInvitation`, `User.disabled_at`, and `CategoryJob.owner_id`. |
| `server/auth.py` | Reject disabled accounts consistently across JWT and API-key authentication. |
| `server/memory_owner_migration.py` | Idempotent pgvector/category-job migration and readiness marker. |
| `server/memory_authorization.py` | Immutable principal, owner-filter construction, caller-owner rejection, and direct-ID checks. |
| `server/main.py` | Apply readiness/principal dependencies to memory routes and start migration before the category worker. |
| `server/routers/entities.py` | Owner-scoped agent/run listing and deletion. |
| `server/category_models.py` | Owner-aware category job values. |
| `server/category_store.py` | Owner-filtered memory snapshots/counts and category-job queries. |
| `server/category_service.py` | Propagate owner scope through counts, jobs, reclassification, and reset cleanup. |
| `server/routers/categories.py` | Derive the principal for owner-sensitive category operations. |
| `server/routers/users.py` | Admin invitation and account lifecycle API. |
| `server/routers/auth.py` | Invitation acceptance and post-bootstrap migration trigger. |
| `tests/server/test_account_auth.py` | Disabled-account authentication regression tests. |
| `tests/server/test_memory_owner_migration.py` | Automatic migration and fail-closed tests. |
| `tests/server/test_memory_authorization.py` | Principal/filter/direct-ID policy unit tests. |
| `tests/server/test_memory_routes_authorization.py` | End-to-end route ownership tests. |
| `tests/server/test_owner_scoped_surfaces.py` | Entity, reset, category count/job/reclassification isolation tests. |
| `tests/server/test_user_invitations.py` | Invitation hashing, lifecycle, replay, and account management tests. |
| `server/dashboard/src/types/api.ts` | User and invitation API types. |
| `server/dashboard/src/utils/api-endpoints.ts` | Users and invitation endpoint constants. |
| `server/dashboard/src/lib/auth.tsx` | Invitation activation and session establishment. |
| `server/dashboard/src/app/(auth)/invite/page.tsx` | Fragment capture, password selection, activation, and sign-in. |
| `server/dashboard/src/app/(root)/dashboard/users/page.tsx` | Minimal admin Users dashboard and one-time URL dialog. |
| `server/dashboard/src/app/(root)/dashboard/components/main-nav.tsx` | Admin-only Users and Requests navigation. |
| `server/dashboard/src/app/(root)/dashboard/categories/page.tsx` | Read-only member category view without admin mutation/reclassification controls. |
| `server/README.md` | Upgrade, invitation, isolation, and recovery contract. |
| `docs/superpowers/specs/2026-08-09-ram0-mcp-design.md` | Reconcile MCP with the implemented principal interface. |
| `docs/superpowers/plans/2026-08-09-ram0-fastmcp.md` | Replace provisional owner logic with the implemented interface after authorization ships. |

## Task 1: Persist account lifecycle state and reject disabled authentication

**Files:**
- Create: `server/alembic/versions/008_multi_user_authorization.py`
- Modify: `server/models.py`
- Modify: `server/auth.py`
- Modify: `server/routers/auth.py`
- Create: `tests/server/test_account_auth.py`

**Interfaces:**
- Produces: `User.disabled_at: datetime | None`.
- Produces: `UserInvitation(id, email, token_hash, created_by, role, expires_at, accepted_at, revoked_at, created_at)`.
- Produces: `CategoryJob.owner_id: uuid.UUID | None`; it remains nullable only until automatic migration completes.
- Produces: `ensure_active_user(user: User) -> User`, raising the existing generic `401` authentication response for disabled accounts.
- Consumes later: Tasks 2 and 5 use the new models; all existing authentication resolvers call `ensure_active_user`.

- [ ] **Step 1: Write failing model and authentication tests**

```python
def test_disabled_jwt_owner_is_rejected(db_session, user, access_token):
    user.disabled_at = datetime.now(timezone.utc)
    db_session.commit()

    with pytest.raises(HTTPException) as error:
        _resolve_user_from_jwt(access_token, db_session)

    assert (error.value.status_code, error.value.detail) == (401, "Invalid or expired credentials.")


def test_disabled_api_key_owner_is_rejected(db_session, user, api_key):
    user.disabled_at = datetime.now(timezone.utc)
    db_session.commit()

    with pytest.raises(HTTPException) as error:
        _resolve_user_from_api_key(api_key, db_session)

    assert (error.value.status_code, error.value.detail) == (401, "Invalid or expired credentials.")


def test_invitation_model_never_has_a_raw_token():
    assert "token" not in UserInvitation.__table__.columns
    assert "token_hash" in UserInvitation.__table__.columns
```

- [ ] **Step 2: Run the tests and confirm the missing schema/auth checks**

Run: `rtk pytest tests/server/test_account_auth.py -q`

Expected: FAIL because `disabled_at`, `UserInvitation`, `CategoryJob.owner_id`, and `ensure_active_user` do not exist.

- [ ] **Step 3: Add migration 008 and the matching SQLAlchemy models**

The migration must add:

```python
op.add_column("users", sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))
op.create_table(
    "user_invitations",
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column("email", sa.String(255), nullable=False),
    sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
    sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("role", sa.String(20), nullable=False, server_default="member"),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
)
op.create_index(
    "uq_user_invitations_pending_email",
    "user_invitations",
    [sa.text("lower(email)")],
    unique=True,
    postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
)
op.add_column("category_jobs", sa.Column("owner_id", sa.Uuid(), nullable=True))
op.create_index("ix_category_jobs_owner_id", "category_jobs", ["owner_id"])
```

Downgrade must remove the index/table/columns in reverse order. `UserInvitation.role` is always `member`; no API accepts a role value.

- [ ] **Step 4: Centralize active-account enforcement**

Add and call this helper from `_resolve_user_from_jwt` and `_resolve_user_from_api_key`:

```python
def ensure_active_user(user: User | None) -> User:
    if user is None or user.disabled_at is not None:
        raise HTTPException(status_code=401, detail="Invalid or expired credentials.")
    return user
```

Update login and refresh to reject disabled accounts with their existing generic public messages. Do not disclose whether the email exists or is disabled.

- [ ] **Step 5: Run focused auth tests and migration syntax checks**

Run: `rtk pytest tests/server/test_account_auth.py -q`

Run: `rtk python -m compileall -q server/alembic/versions/008_multi_user_authorization.py server/models.py server/auth.py server/routers/auth.py`

Expected: PASS.

- [ ] **Step 6: Commit the lifecycle foundation**

```bash
git add server/alembic/versions/008_multi_user_authorization.py server/models.py server/auth.py server/routers/auth.py tests/server/test_account_auth.py
git commit -m "feat(server): add multi-user account state"
```

## Task 2: Automatically claim legacy memories for the sole administrator

**Files:**
- Create: `server/memory_owner_migration.py`
- Modify: `server/main.py`
- Modify: `server/routers/auth.py`
- Create: `tests/server/test_memory_owner_migration.py`

**Interfaces:**
- Produces: `OWNERSHIP_VERSION_KEY = "memory_ownership_version"` and `OWNERSHIP_VERSION = "1"`.
- Produces: `migrate_legacy_ownership(session_factory=SessionLocal, memory_factory=get_memory_instance) -> OwnershipMigrationResult`.
- Produces: `require_ownership_ready(session_factory=SessionLocal) -> None`, raising HTTP 503 when version 1 is absent.
- Consumes: Task 1 models and the configured pgvector store's `list(top_k=None)` and `_patch_payload(...)` methods.
- Consumed later: memory-principal and invitation dependencies call `require_ownership_ready`.

- [ ] **Step 1: Write failing migration-state tests with a fake pgvector store**

```python
def test_empty_install_marks_version_ready(migration_context):
    result = migrate_legacy_ownership(**migration_context(memories=[], users=[], jobs=[]))
    assert result.state == "ready"
    assert result.migrated_memories == 0


def test_sole_admin_claims_every_memory_and_job(migration_context, admin_id):
    memories = [
        row("m1", {"data": "one", "user_id": "legacy", "agent_id": "agent-a"}),
        row("m2", {"data": "two", "categories": ["work"]}),
    ]
    result = migrate_legacy_ownership(**migration_context(memories=memories, users=[admin(admin_id)], jobs=[job(None)]))

    assert result.state == "ready"
    assert all(item.payload["user_id"] == str(admin_id) for item in memories)
    assert memories[0].payload["agent_id"] == "agent-a"
    assert memories[1].payload["categories"] == ["work"]


def test_multiple_preexisting_accounts_fail_closed(migration_context):
    context = migration_context(memories=[row("m1", {"user_id": "legacy"})], users=[admin(), member()], jobs=[])
    result = migrate_legacy_ownership(**context)
    assert result.state == "blocked"
    with pytest.raises(HTTPException) as error:
        require_ownership_ready(context["session_factory"])
    assert error.value.status_code == 503
```

Also test no-admin waiting state, unsupported provider, `_patch_payload` interruption followed by a successful rerun, post-write verification failure, and preservation of the complete preexisting payload except `user_id`.

- [ ] **Step 2: Run migration tests and confirm the module is absent**

Run: `rtk pytest tests/server/test_memory_owner_migration.py -q`

Expected: FAIL importing `memory_owner_migration`.

- [ ] **Step 3: Implement the idempotent migration state machine**

Use these public result states:

```python
class OwnershipMigrationResult(NamedTuple):
    state: Literal["ready", "waiting_for_admin", "blocked"]
    migrated_memories: int
    migrated_jobs: int
```

Algorithm:

```python
if settings_version == OWNERSHIP_VERSION:
    return OwnershipMigrationResult("ready", 0, 0)
rows = flatten(memory.vector_store.list(top_k=None))
jobs_without_owner = select(CategoryJob).where(CategoryJob.owner_id.is_(None))
if not rows and not jobs_without_owner:
    persist_version_1()
elif no_users:
    return waiting_for_admin
elif users != [one_admin] or provider != "pgvector":
    return blocked
else:
    for row in rows:
        memory.vector_store._patch_payload(
            row.id,
            {"user_id": str(admin.id)},
            expected={"user_id": (row.payload or {}).get("user_id")},
        )
    assign_null_job_owners(admin.id)
    rescan_and_require_every_owner(admin.id)
    persist_version_1()
```

Never log payloads or old namespace values. Log only state and counts. The marker is written last, after memory and category-job verification.

- [ ] **Step 4: Wire migration before category-worker startup and after bootstrap registration**

In `category_lifespan`, call migration before `initialize_category_runtime()`. A waiting/blocked result keeps the API process alive for setup and diagnostics, but protected memory/invitation dependencies return 503.

After the first admin is committed in `/auth/register`, call migration again before returning tokens. If provider migration fails, return the generic maintenance 503 while keeping the new admin account usable for a later login/retry.

- [ ] **Step 5: Run migration and startup regression tests**

Run: `rtk pytest tests/server/test_memory_owner_migration.py tests/server/test_category_worker.py -q`

Expected: PASS; worker initialization occurs only after a ready migration.

- [ ] **Step 6: Commit automatic ownership migration**

```bash
git add server/memory_owner_migration.py server/main.py server/routers/auth.py tests/server/test_memory_owner_migration.py
git commit -m "feat(server): migrate legacy memory ownership"
```

## Task 3: Enforce the principal on every core memory route

**Files:**
- Create: `server/memory_authorization.py`
- Modify: `server/main.py`
- Create: `tests/server/test_memory_authorization.py`
- Create: `tests/server/test_memory_routes_authorization.py`

**Interfaces:**
- Produces: `MemoryPrincipal(owner_id: str)`.
- Produces: `principal_for(user: User) -> MemoryPrincipal` for routes that already require a specific role.
- Produces: `require_memory_principal(user: User = Depends(require_auth)) -> MemoryPrincipal`.
- Produces: `reject_client_owner(value: object) -> None`, recursively rejecting any `user_id` key.
- Produces: `owner_filters(principal, *, agent_id=None, run_id=None, extra=None) -> dict[str, object]`.
- Produces: `require_owned_memory(memory_id, principal, memory) -> OutputData`, returning identical 404 for missing/foreign rows.
- Consumes: Task 2 `require_ownership_ready`.

- [ ] **Step 1: Write failing policy tests**

```python
def test_owner_filters_cannot_be_overridden(principal):
    assert owner_filters(principal, agent_id="a", extra={"categories": {"in": ["work"]}}) == {
        "user_id": principal.owner_id,
        "agent_id": "a",
        "categories": {"in": ["work"]},
    }


@pytest.mark.parametrize("value", [
    {"user_id": "other"},
    {"AND": [{"agent_id": "a"}, {"user_id": {"in": ["other"]}}]},
])
def test_nested_owner_selectors_are_rejected(value):
    with pytest.raises(HTTPException) as error:
        reject_client_owner(value)
    assert error.value.status_code == 422


def test_missing_and_foreign_memory_are_indistinguishable(memory, principal):
    for row in (None, vector_row(user_id="other")):
        memory.vector_store.get.return_value = row
        with pytest.raises(HTTPException) as error:
            require_owned_memory("memory-id", principal, memory)
        assert (error.value.status_code, error.value.detail) == (404, "Memory not found.")
```

- [ ] **Step 2: Write failing route tests for both JWT and API-key principals**

Cover add without identifiers; caller `user_id` rejection from top-level fields, nested filters, create metadata, and update metadata; owner-scoped list/search; direct get/update/delete/history; whole-owner bulk delete; and owner-only reset. Assert the mocked memory engine always receives the authenticated UUID and never a foreign UUID.

Run: `rtk pytest tests/server/test_memory_authorization.py tests/server/test_memory_routes_authorization.py -q`

Expected: FAIL because routes still use `verify_auth` and caller identifiers.

- [ ] **Step 3: Implement the immutable policy seam**

```python
@dataclass(frozen=True, slots=True)
class MemoryPrincipal:
    owner_id: str


def principal_for(user: User) -> MemoryPrincipal:
    return MemoryPrincipal(owner_id=str(user.id))


def require_memory_principal(user: User = Depends(require_auth)) -> MemoryPrincipal:
    require_ownership_ready()
    return principal_for(user)
```

The owner-filter merger must reject an `extra` mapping containing `user_id` at any nesting depth before adding the canonical owner.

- [ ] **Step 4: Apply the principal to every route in `server/main.py`**

Required route changes:

```python
# add
reject_client_owner(memory_create.user_id and {"user_id": memory_create.user_id})
reject_client_owner(memory_create.metadata)
params["user_id"] = principal.owner_id

# list/search
filters = owner_filters(principal, agent_id=agent_id, run_id=run_id, extra=category_or_search_filters)

# direct ID
require_owned_memory(memory_id, principal, memory)

# update
reject_client_owner(updated_memory.metadata)

# bulk delete and reset
memory.delete_all(user_id=principal.owner_id, agent_id=agent_id, run_id=run_id)
```

`/reset` must call owner-scoped `delete_all`, not `memory.reset()`. Keep category cleanup owner-aware through the interface introduced in Task 4.

- [ ] **Step 5: Run route tests and category route regression tests**

Run: `rtk pytest tests/server/test_memory_authorization.py tests/server/test_memory_routes_authorization.py tests/server/test_category_memory_routes.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the core isolation boundary**

```bash
git add server/memory_authorization.py server/main.py tests/server/test_memory_authorization.py tests/server/test_memory_routes_authorization.py
git commit -m "feat(server): enforce account memory ownership"
```

## Task 4: Scope entities and category operations to the principal

**Files:**
- Modify: `server/routers/entities.py`
- Modify: `server/category_models.py`
- Modify: `server/category_store.py`
- Modify: `server/category_service.py`
- Modify: `server/routers/categories.py`
- Modify: `server/main.py`
- Create: `tests/server/test_owner_scoped_surfaces.py`
- Modify: `tests/server/test_categories_router.py`
- Modify: `tests/server/test_category_service.py`
- Modify: `tests/server/test_category_store.py`

**Interfaces:**
- Produces: `MemoryCategoryStore.category_counts(owner_id: str) -> dict[str, int]` and owner-filtered snapshot enumeration.
- Produces: `CategoryJobStore.list(..., owner_id: uuid.UUID)` and owner-aware enqueue values.
- Produces: `CategoryService.get_catalog_view(owner_id)`, `list_jobs(..., owner_id)`, `preview_reclassification(..., owner_id)`, `start_reclassification(..., owner_id)`, and `after_owner_reset(owner_id)`.
- Consumes: Task 3 `MemoryPrincipal`, `owner_filters`, and owner-scoped reset.

- [ ] **Step 1: Write failing isolation tests**

```python
OWNER_A = "00000000-0000-0000-0000-000000000001"


def test_entities_scan_only_the_principal_owner(entity_client, principal):
    response = entity_client.get("/entities")
    assert vector_store.list.call_args.kwargs["filters"] == {"user_id": principal.owner_id}
    assert all(item["type"] in {"agent", "run"} for item in response.json())


def test_category_counts_are_owner_scoped(memory_store):
    memory_store.category_counts(OWNER_A)
    assert memory_store.vector_store.list.call_args.kwargs["filters"] == {"user_id": OWNER_A}


def test_admin_reclassification_does_not_enqueue_other_owner(service):
    service.start_reclassification(scope="all", confirm="RECLASSIFY", owner_id=OWNER_A)
    assert {job.owner_id for job in service.job_store.created} == {UUID(OWNER_A)}
```

Also assert category job API rows never contain another owner's memory ID and owner reset cancels only that owner's pending jobs.

- [ ] **Step 2: Run the focused surface tests and confirm global scans fail them**

Run: `rtk pytest tests/server/test_owner_scoped_surfaces.py tests/server/test_categories_router.py tests/server/test_category_service.py tests/server/test_category_store.py -q`

Expected: FAIL because current entity/category methods scan globally.

- [ ] **Step 3: Add owner propagation to category storage and service methods**

Every memory snapshot/count scan uses:

```python
rows = self._memory_factory().vector_store.list(filters={"user_id": owner_id}, top_k=None)
```

Every new `CategoryJob` receives `owner_id=UUID(snapshot.user_id)`. Job listing and owner reset include `CategoryJob.owner_id == owner_uuid`. The background worker may still process jobs from all owners; owner scope controls creation, operator views, reclassification, and cancellation rather than worker scheduling.

- [ ] **Step 4: Derive principals in entity/category routers**

`GET /categories` returns the global catalog plus counts for the current principal only. Catalog create/update/delete remain global `require_admin` operations but return the admin principal's counts. Job listing and reclassification require admin and pass that admin's owner UUID; they never operate on member memories.

`/entities` removes `user` from `EntityType`, filters the scan by canonical `user_id`, and combines the canonical owner with agent/run deletion.

- [ ] **Step 5: Connect owner reset cleanup and run all category tests**

Replace global `after_reset()` in `/reset` with `after_owner_reset(principal.owner_id)`.

Run: `rtk pytest tests/server/test_owner_scoped_surfaces.py tests/server/test_categories_router.py tests/server/test_category_service.py tests/server/test_category_store.py tests/server/test_category_memory_routes.py tests/server/test_category_worker.py -q`

Expected: PASS.

- [ ] **Step 6: Commit indirect-surface isolation**

```bash
git add server/routers/entities.py server/category_models.py server/category_store.py server/category_service.py server/routers/categories.py server/main.py tests/server/test_owner_scoped_surfaces.py tests/server/test_categories_router.py tests/server/test_category_service.py tests/server/test_category_store.py
git commit -m "feat(server): scope memory-adjacent operations"
```

## Task 5: Add copied-link invitations and account lifecycle APIs

**Files:**
- Create: `server/routers/users.py`
- Modify: `server/routers/auth.py`
- Modify: `server/main.py`
- Create: `tests/server/test_user_invitations.py`

**Interfaces:**
- Produces: `POST /admin/invitations`, `DELETE /admin/invitations/{id}`, `GET /admin/users`, `POST /admin/users/{id}/disable`, and `POST /admin/users/{id}/restore`.
- Produces: `POST /auth/invitations/accept` returning the existing `TokenResponse`.
- Produces: `hash_invitation_token(token: str) -> str` using SHA-256 and `generate_invitation_token() -> str` using `secrets.token_urlsafe(32)`.
- Consumes: Task 2 readiness gate and Task 1 invitation/account models.

- [ ] **Step 1: Write failing invitation and lifecycle tests**

```python
def test_create_invitation_returns_raw_url_once(admin_client):
    response = admin_client.post("/admin/invitations", json={"email": "member@example.com"})
    assert response.status_code == 201
    assert response.json()["invite_url"].startswith("https://ram0.example.lan/invite#token=")
    assert "token" not in admin_client.get("/admin/users").text
    assert len(saved_invitation.token_hash) == 64


@pytest.mark.parametrize("state", ["unknown", "expired", "revoked", "accepted"])
def test_invalid_invitation_states_share_one_error(invitation_client, state):
    response = invitation_client.accept(state)
    assert (response.status_code, response.json()["detail"]) == (400, "Invitation is invalid or expired.")


def test_acceptance_is_single_use(invitation_client, token):
    first = invitation_client.post("/auth/invitations/accept", json={"token": token, "password": "correct horse"})
    second = invitation_client.post("/auth/invitations/accept", json={"token": token, "password": "correct horse"})
    assert first.status_code == 200
    assert second.status_code == 400


def test_admin_cannot_disable_self_or_admin(admin_client, admin):
    response = admin_client.post(f"/admin/users/{admin.id}/disable")
    assert response.status_code == 409
```

Also cover normalized duplicate active/pending email, profile-email collision with a pending invitation, non-admin 403, seven-day expiry, role fixed to member, email used as initial name, migration-not-ready 503, revoke, restore, invalid UUID 404, and a PostgreSQL `SELECT ... FOR UPDATE` concurrency test when `TEST_POSTGRES_URL` is configured.

- [ ] **Step 2: Run invitation tests and confirm routes are absent**

Run: `rtk pytest tests/server/test_user_invitations.py -q`

Expected: FAIL with missing router/routes.

- [ ] **Step 3: Implement token helpers and transactional acceptance**

```python
INVITATION_LIFETIME = timedelta(days=7)

def generate_invitation_token() -> str:
    return secrets.token_urlsafe(32)

def hash_invitation_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
```

Acceptance performs a `SELECT ... FOR UPDATE` by token hash, applies every validity check inside the transaction, creates `User(name=email, email=email, role="member")`, sets `accepted_at`, commits once, and then creates access/refresh tokens. It never accepts `role` or `name` from the caller.

Apply the existing limiter to invitation creation and acceptance at `10/minute` per client. Rate-limit responses must not reveal whether an email or token is valid.

- [ ] **Step 4: Implement admin list/create/revoke/disable/restore routes**

Both invitation creation and acceptance call `require_ownership_ready`, so no member account can be created before legacy ownership is verified. `POST /admin/invitations` returns `{id, email, expires_at, invite_url}` once, using `DASHBOARD_URL.rstrip('/') + '/invite#token=' + token`. List responses contain no token or token hash.

`GET /admin/users` labels each unconsumed invitation as `pending` or `expired` from the server clock. Expired invitations remain revocable but never become valid again.

Disable and restore operate only on role `member`; attempts against any admin return `409 Administrator accounts cannot be disabled.` Existing API keys are not rewritten or revoked because every use checks the owner's active state.

Update the existing profile-email route to reject an email held by an active pending invitation with `409 Email is already in use.` This keeps account and invitation uniqueness true after invitation creation as well as before it.

- [ ] **Step 5: Run invitation, auth, and API-key tests**

Run: `rtk pytest tests/server/test_user_invitations.py tests/server/test_account_auth.py -q`

Expected: PASS, including the optional real-Postgres concurrency case when configured.

- [ ] **Step 6: Commit invitation APIs**

```bash
git add server/routers/users.py server/routers/auth.py server/main.py tests/server/test_user_invitations.py
git commit -m "feat(server): add copied-link user invitations"
```

## Task 6: Build the minimal Users and invitation dashboard

**Files:**
- Modify: `server/dashboard/src/types/api.ts`
- Modify: `server/dashboard/src/utils/api-endpoints.ts`
- Modify: `server/dashboard/src/lib/auth.tsx`
- Create: `server/dashboard/src/app/(auth)/invite/page.tsx`
- Create: `server/dashboard/src/app/(root)/dashboard/users/page.tsx`
- Modify: `server/dashboard/src/app/(root)/dashboard/components/main-nav.tsx`
- Modify: `server/dashboard/src/app/(root)/dashboard/categories/page.tsx`

**Interfaces:**
- Produces: `AdminUser`, `PendingInvitation`, `UsersResponse`, and `InvitationCreateResponse` TypeScript types.
- Produces: `acceptInvitation(token: string, password: string): Promise<void>` on `AuthContextValue`.
- Consumes: Task 5 HTTP endpoints.

- [ ] **Step 1: Add exact API types and endpoint constants**

```typescript
export interface AdminUser {
  id: string;
  name: string;
  email: string;
  role: "admin" | "member";
  created_at: string;
  disabled_at: string | null;
}

export interface PendingInvitation {
  id: string;
  email: string;
  created_at: string;
  expires_at: string;
  status: "pending" | "expired";
}

export interface UsersResponse {
  users: AdminUser[];
  pending_invitations: PendingInvitation[];
}

export const USER_ENDPOINTS = {
  BASE: "/admin/users",
  INVITATIONS: "/admin/invitations",
  INVITATION_BY_ID: (id: string) => `/admin/invitations/${id}`,
  DISABLE: (id: string) => `/admin/users/${id}/disable`,
  RESTORE: (id: string) => `/admin/users/${id}/restore`,
} as const;
```

- [ ] **Step 2: Implement invite activation session handling**

Add `AUTH_ENDPOINTS.ACCEPT_INVITATION`. `acceptInvitation` posts `{token, password}`, stores the returned refresh token through the existing HTTP-only cookie route, sets the access token, and loads `/auth/me`, matching login behavior.

The public page captures and removes the fragment exactly once:

```typescript
useEffect(() => {
  const token = new URLSearchParams(window.location.hash.slice(1)).get("token");
  window.history.replaceState(null, "", "/invite");
  setInviteToken(token);
}, []);
```

Require matching passwords of at least eight characters. A missing token shows `This invitation link is unavailable. Ask the administrator for a new one.` Successful activation navigates to `/dashboard/memories`.

- [ ] **Step 3: Build the admin Users page using existing dashboard components**

Follow the API Keys page's `Dialog`, `DataTable`, `TableSkeleton`, `EmptyState`, `DeleteConfirmationModal`, toast, and copy-button patterns. The dialog starts with one email field. After creation it replaces the form with a read-only URL, Copy button, and this warning:

```text
Copy this link now. It won't be shown again.
```

Closing the dialog clears `inviteUrl`, `email`, and copied state. Pending rows show email, created, expires, and Revoke only. Active/disabled rows show role/status and confirmed Disable/Restore actions.

- [ ] **Step 4: Enforce admin-only navigation and page behavior**

Use `useAuth()` in `MainNav`: include Requests and Users only when `isAdmin`; keep Memories, Entities, Categories, API Keys, Configuration, and Settings visible to all authenticated accounts. The existing Configuration page remains read-only for members. The Categories page shows the global catalog and that member's own counts, but renders `CategoryEditor` and `ReclassificationPanel` only for admins. The Users page redirects a resolved non-admin user to `/dashboard/memories`; backend 403 remains authoritative.

- [ ] **Step 5: Format, type-check, and build the dashboard**

Run: `rtk pnpm -C server/dashboard exec prettier --write src/types/api.ts src/utils/api-endpoints.ts src/lib/auth.tsx 'src/app/(auth)/invite/page.tsx' 'src/app/(root)/dashboard/users/page.tsx' 'src/app/(root)/dashboard/components/main-nav.tsx' 'src/app/(root)/dashboard/categories/page.tsx'`

Run: `rtk pnpm -C server/dashboard run lint`

Run: `rtk pnpm -C server/dashboard run typecheck`

Run: `rtk pnpm -C server/dashboard run build`

Expected: all PASS.

- [ ] **Step 6: Manually verify the real dashboard flow**

In a local running stack: create an invitation, verify the raw link appears only in the dialog, copy it, close the dialog and confirm it cannot be recovered, accept it in a private browser session, confirm automatic sign-in, verify member navigation lacks Users/Requests, and confirm direct `/dashboard/users` access receives the backend denial path.

- [ ] **Step 7: Commit the dashboard**

```bash
git add server/dashboard/src/types/api.ts server/dashboard/src/utils/api-endpoints.ts server/dashboard/src/lib/auth.tsx 'server/dashboard/src/app/(auth)/invite/page.tsx' 'server/dashboard/src/app/(root)/dashboard/users/page.tsx' 'server/dashboard/src/app/(root)/dashboard/components/main-nav.tsx' 'server/dashboard/src/app/(root)/dashboard/categories/page.tsx'
git commit -m "feat(dashboard): add user invitation management"
```

## Task 7: Document, reconcile MCP, and verify the complete feature

**Files:**
- Modify: `server/README.md`
- Modify: `docs/superpowers/specs/2026-08-09-ram0-mcp-design.md`
- Modify: `docs/superpowers/plans/2026-08-09-ram0-fastmcp.md`

**Interfaces:**
- Consumes: the implemented `MemoryPrincipal`, readiness, and ownership-check interfaces from Tasks 2–4.
- Produces: operator upgrade/account instructions and an MCP plan that reuses the authorization seam rather than duplicating owner logic.

- [ ] **Step 1: Document the multi-user contract and automatic upgrade**

Add README sections covering:

- automatic sole-admin claim of legacy memories during Mem0-to-Ram0 replacement;
- fail-closed multiple-account/unsupported-store diagnostics;
- copied, seven-day, one-time invitation links and revoke/recreate recovery;
- account-derived REST `user_id` and rejection of caller owner selectors;
- owner-scoped reset/entities/categories and absence of admin memory bypass;
- disable/restore behavior across JWTs, refresh tokens, and API keys;
- pre-upgrade PostgreSQL backup requirement without embedding credentials.

- [ ] **Step 2: Reconcile the deferred MCP documents**

Update the MCP spec and plan so `mcp_auth` resolves the API-key owner and then calls the implemented `MemoryPrincipal`/ownership helpers. Remove any duplicate direct `user_id` policy and state that MCP is unavailable until ownership version 1 is ready. Preserve the six approved MCP tools and bearer-only transport contract.

- [ ] **Step 3: Run backend formatting and focused verification**

Run: `rtk ruff format --check server tests/server`

Run: `rtk ruff check server tests/server`

Run: `rtk pytest tests/server/test_account_auth.py tests/server/test_memory_owner_migration.py tests/server/test_memory_authorization.py tests/server/test_memory_routes_authorization.py tests/server/test_owner_scoped_surfaces.py tests/server/test_user_invitations.py -q`

Expected: all PASS.

- [ ] **Step 4: Run the full server and dashboard verification**

Run: `rtk pytest tests/server -q`

Run: `rtk pnpm -C server/dashboard run lint`

Run: `rtk pnpm -C server/dashboard run typecheck`

Run: `rtk pnpm -C server/dashboard run build`

Expected: all PASS.

- [ ] **Step 5: Verify migration and cross-user isolation in the real stack**

Use synthetic memories only. Before upgrading, create memories under two legacy `user_id` values; back up PostgreSQL; start the new server with one admin; verify both memories retain IDs/content/metadata/categories but now carry the admin UUID. Invite a member, create one memory per account, and prove list/search/get/update/delete/history/reset/entities/category counts/jobs/reclassification cannot cross the account boundary through either JWT or API key. Confirm request logs contain no invitation token, password, API key, search text, or memory content.

- [ ] **Step 6: Inspect the complete diff and commit documentation**

Run: `rtk git diff --check`

Run: `rtk git status --short`

Confirm the diff contains no CI workflow changes, secrets, generated dashboard output, or unrelated edits.

```bash
git add server/README.md docs/superpowers/specs/2026-08-09-ram0-mcp-design.md docs/superpowers/plans/2026-08-09-ram0-fastmcp.md
git commit -m "docs: document multi-user authorization"
```

- [ ] **Step 7: Perform final completion review**

Use `superpowers:verification-before-completion`, report exact command results, and do not claim the feature safe if migration, cross-user, backend, or dashboard verification is missing.
