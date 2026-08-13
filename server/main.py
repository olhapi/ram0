# Modified for Ram0; see NOTICE and repository history.

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import telemetry
from app_scope import validate_app_id
from auth import ADMIN_API_KEY, AUTH_DISABLED, JWT_SECRET, require_admin, verify_auth
from category_models import CATEGORY_ORIGIN_KEY, parse_per_call_categories, promote_category_fields
from category_runtime import (
    get_category_service,
    get_category_worker,
    get_initialized_category_worker,
    initialize_category_runtime,
)
from dashboard_url import dashboard_origin
from db import SessionLocal
from dotenv import load_dotenv
from errors import (
    UpstreamError,
    install_request_id_logging,
    new_request_id,
    request_id_var,
    upstream_error,
    upstream_error_handler,
)
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from memory_owner_migration import migrate_legacy_ownership
import mcp_auth
from mcp_server import create_mcp_http_app, mcp_authenticated_app
from memory_authorization import (
    MemoryPrincipal,
    owner_filters,
    reject_client_owner,
    require_memory_principal,
    require_owned_memory,
)
from models import RequestLog, User
from pydantic import BaseModel, Field, ValidationError as PydanticValidationError
from rate_limit import limiter
from routers import api_keys as api_keys_router
from routers import auth as auth_router
from routers import categories as categories_router
from routers import entities as entities_router
from routers import requests as requests_router
from routers import users as users_router
from schemas import MessageResponse
from server_state import (
    get_current_config,
    get_memory_instance,
    initialize_state,
    set_session_factory,
    update_config,
)
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import func, select

from mem0.exceptions import ValidationError as Mem0ValidationError

load_dotenv()

install_request_id_logging()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - [%(request_id)s] %(message)s")

MIN_KEY_LENGTH = 16
SENSITIVE_CONFIG_KEYS = {
    "admin_api_key",
    "api_key",
    "authorization",
    "jwt_secret",
    "password",
    "password_hash",
    "secret",
    "token",
}
SKIPPED_REQUEST_LOG_PATHS = {"/api/health", "/docs", "/redoc", "/openapi.json"}
SKIPPED_REQUEST_LOG_PREFIXES = ("/requests",)

BUNDLED_LLM_PROVIDERS = ("openai", "anthropic", "gemini")
BUNDLED_EMBEDDER_PROVIDERS = ("openai", "gemini")


def _warn_if_unconfigured() -> None:
    """Pre-auth deployments upgrading into this build will 401 everywhere until
    an admin key or admin user exists. Surface the fix before the support tickets."""
    try:
        with SessionLocal() as session:
            if session.scalar(select(func.count(User.id))) > 0:
                return
    except Exception:
        return

    logging.warning(
        "\n%s\n"
        "  Auth is enabled by default and this server has no admin configured.\n"
        "  Protected endpoints will return 401 until you either:\n"
        "    1. Set ADMIN_API_KEY=<long-random-value>  (fastest, no client changes)\n"
        "    2. Register an admin at http://<host>:3000/setup\n"
        "    3. Set AUTH_DISABLED=true                 (local development only)\n"
        "  Docs: https://docs.mem0.ai/open-source/features/rest-api#authentication\n"
        "%s",
        "=" * 72,
        "=" * 72,
    )


if not AUTH_DISABLED and not JWT_SECRET:
    raise RuntimeError(
        "JWT_SECRET is required. Set it in .env (generate with `openssl rand -base64 48`) "
        "or set AUTH_DISABLED=true for local development only."
    )

if AUTH_DISABLED:
    logging.warning("AUTH_DISABLED is enabled. Protected endpoints are open for local development only.")
elif ADMIN_API_KEY and len(ADMIN_API_KEY) < MIN_KEY_LENGTH:
    logging.warning(
        "ADMIN_API_KEY is shorter than %d characters - consider using a longer key for production.",
        MIN_KEY_LENGTH,
    )
elif not ADMIN_API_KEY:
    _warn_if_unconfigured()

telemetry.log_status()

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "postgres")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "postgres")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "postgres")
POSTGRES_COLLECTION_NAME = os.environ.get("POSTGRES_COLLECTION_NAME", "memories")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
HISTORY_DB_PATH = os.environ.get("HISTORY_DB_PATH", "/app/history/history.db")
DEFAULT_LLM_MODEL = os.environ.get("MEM0_DEFAULT_LLM_MODEL", "gpt-5-mini")
DEFAULT_EMBEDDER_MODEL = os.environ.get("MEM0_DEFAULT_EMBEDDER_MODEL", "text-embedding-3-small")

DEFAULT_CONFIG = {
    "version": "v1.1",
    "vector_store": {
        "provider": "pgvector",
        "config": {
            "host": POSTGRES_HOST,
            "port": int(POSTGRES_PORT),
            "dbname": POSTGRES_DB,
            "user": POSTGRES_USER,
            "password": POSTGRES_PASSWORD,
            "collection_name": POSTGRES_COLLECTION_NAME,
        },
    },
    "llm": {
        "provider": "openai",
        "config": {"api_key": OPENAI_API_KEY, "temperature": 0.2, "model": DEFAULT_LLM_MODEL},
    },
    "embedder": {"provider": "openai", "config": {"api_key": OPENAI_API_KEY, "model": DEFAULT_EMBEDDER_MODEL}},
    "history_db_path": HISTORY_DB_PATH,
}


set_session_factory(SessionLocal)
initialize_state(DEFAULT_CONFIG)


@asynccontextmanager
async def category_lifespan(_: FastAPI):
    """Start the category runtime once and always request worker shutdown."""
    worker = None
    try:
        migration = migrate_legacy_ownership()
        if migration.state == "ready":
            initialize_category_runtime()
            worker = get_category_worker()
        else:
            logging.warning(
                "memory_ownership_migration state=%s migrated_memories=%d migrated_jobs=%d",
                migration.state,
                migration.migrated_memories,
                migration.migrated_jobs,
            )
        yield
    finally:
        if worker is None:
            worker = get_initialized_category_worker()
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                logging.warning("category_worker_stop_failed error_code=worker_stop_failed")


@asynccontextmanager
async def application_lifespan(app: FastAPI):
    """Compose category startup with a fresh FastMCP session lifecycle."""
    async with category_lifespan(app):
        _, active_mcp_http_app = create_mcp_http_app()
        previous_mcp_http_app = mcp_authenticated_app.app
        mcp_authenticated_app.app = active_mcp_http_app
        try:
            async with active_mcp_http_app.lifespan(app):
                yield
        finally:
            mcp_authenticated_app.app = previous_mcp_http_app


app = FastAPI(
    title="Mem0 REST APIs",
    description=(
        "A REST API for managing and searching memories for your AI Agents and Apps.\n\n"
        "## Authentication\n"
        "Supports Bearer JWT tokens or per-user API keys, plus the legacy `X-API-Key` header, "
        "or the legacy `ADMIN_API_KEY` environment variable. Set `AUTH_DISABLED=true` for local development only."
    ),
    version="1.0.0",
    redirect_slashes=False,
    lifespan=application_lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_exception_handler(UpstreamError, upstream_error_handler)
DASHBOARD_URL = dashboard_origin()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[DASHBOARD_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(api_keys_router.router)
app.include_router(categories_router.router)
app.include_router(entities_router.router)
app.include_router(requests_router.router)
app.include_router(users_router.router)


@app.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
async def mcp_root(request: Request):
    """Authenticate the slashless MCP URL before its protocol redirect."""
    await mcp_auth.require_mcp_bearer(request)
    destination = f"{request.url.path}/"
    if request.url.query:
        destination = f"{destination}?{request.url.query}"
    return RedirectResponse(url=destination, status_code=307)


app.mount("/mcp", mcp_authenticated_app)


class Message(BaseModel):
    role: str = Field(..., description="Role of the message (user or assistant).")
    content: str = Field(..., description="Message content.")


class MemoryCreate(BaseModel):
    messages: List[Message] = Field(..., description="List of messages to store.")
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    app_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    expiration_date: Optional[str] = Field(None, description="Expiration date in YYYY-MM-DD format.")
    infer: Optional[bool] = Field(None, description="Whether to extract facts from messages. Defaults to True.")
    memory_type: Optional[str] = Field(None, description="Type of memory to store (e.g. 'core').")
    prompt: Optional[str] = Field(None, description="Custom prompt to use for fact extraction.")
    custom_categories: Optional[List[Dict[str, str]]] = Field(
        None,
        description="One-call category definitions as one-key name-to-description objects.",
    )


class MemoryUpdate(BaseModel):
    text: Optional[str] = Field(None, description="New content to update the memory with.")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Metadata to update.")
    expiration_date: Optional[str] = Field(None, description="Expiration date in YYYY-MM-DD format, or null to clear.")


class SearchRequest(BaseModel):
    query: str = Field(..., description="Search query.")
    user_id: Optional[str] = Field(None, description="Deprecated: pass inside `filters` instead.", deprecated=True)
    run_id: Optional[str] = Field(None, description="Deprecated: pass inside `filters` instead.", deprecated=True)
    agent_id: Optional[str] = Field(None, description="Deprecated: pass inside `filters` instead.", deprecated=True)
    app_id: Optional[str] = None
    filters: Optional[Dict[str, Any]] = None
    top_k: Optional[int] = Field(None, description="Maximum number of results to return.")
    threshold: Optional[float] = Field(None, description="Minimum similarity score for results.")
    explain: Optional[bool] = Field(None, description="Include score details for each search result.")
    show_expired: Optional[bool] = Field(None, description="Include expired memories.")


class GenerateInstructionsRequest(BaseModel):
    use_case: str = Field(..., description="Description of what the user will use Mem0 for.")


def _client_error(exc: Exception) -> HTTPException:
    """Map core validation / not-found errors to 4xx so clients can tell a bad
    request from an upstream outage. 'not found' is a 404, everything else a 400."""
    detail = str(exc)
    status_code = 404 if isinstance(exc, ValueError) and "not found" in detail.lower() else 400
    return HTTPException(status_code=status_code, detail=detail)


def _validated_app_id(value: Optional[str]) -> Optional[str]:
    """Validate the trusted top-level project selector as a client input error."""
    if value is None:
        return None
    try:
        return validate_app_id(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _redact_config(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {item_key: _redact_config(item_value, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact_config(item_value, key) for item_value in value]
    if key is not None and key.lower() in SENSITIVE_CONFIG_KEYS:
        return "[redacted]" if value else value
    return value


def _validate_bundled_providers(config: Dict[str, Any]) -> None:
    llm = config.get("llm")
    if isinstance(llm, dict) and (provider := llm.get("provider")) and provider not in BUNDLED_LLM_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"LLM provider '{provider}' is not bundled in this image. "
                f"Bundled providers: {', '.join(BUNDLED_LLM_PROVIDERS)}. "
                "To use another provider, install its Python package, rebuild the container, "
                "and extend BUNDLED_LLM_PROVIDERS in server/main.py."
            ),
        )

    embedder = config.get("embedder")
    if (
        isinstance(embedder, dict)
        and (provider := embedder.get("provider"))
        and provider not in BUNDLED_EMBEDDER_PROVIDERS
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Embedder provider '{provider}' is not bundled in this image. "
                f"Bundled providers: {', '.join(BUNDLED_EMBEDDER_PROVIDERS)}. "
                "To use another provider, install its Python package, rebuild the container, "
                "and extend BUNDLED_EMBEDDER_PROVIDERS in server/main.py."
            ),
        )


def _should_log_request(request: Request) -> bool:
    if request.method == "OPTIONS":
        return False
    path = request.url.path
    if path in SKIPPED_REQUEST_LOG_PATHS:
        return False
    return not path.startswith(SKIPPED_REQUEST_LOG_PREFIXES)


def _persist_request_log(method: str, path: str, status_code: int, latency_ms: float, auth_type: str) -> None:
    session = SessionLocal()

    try:
        session.add(
            RequestLog(
                method=method,
                path=path,
                status_code=status_code,
                latency_ms=latency_ms,
                auth_type=auth_type,
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        logging.exception("Failed to persist request log")
    finally:
        session.close()


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request.state.auth_type = getattr(request.state, "auth_type", "none")
    rid = new_request_id()
    token = request_id_var.set(rid)
    start = time.perf_counter()
    status_code = 500

    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
    except Exception:
        status_code = 500
        raise
    finally:
        request_id_var.reset(token)
        if _should_log_request(request):
            asyncio.get_running_loop().run_in_executor(
                None,
                _persist_request_log,
                request.method,
                request.url.path,
                status_code,
                round((time.perf_counter() - start) * 1000, 2),
                getattr(request.state, "auth_type", "none"),
            )


@app.get("/configure", summary="Get current Mem0 configuration")
def get_config(_auth=Depends(verify_auth)):
    return _redact_config(get_current_config())


@app.get("/configure/providers", summary="List bundled LLM and embedder providers")
def list_bundled_providers(_auth=Depends(verify_auth)):
    return {"llm": list(BUNDLED_LLM_PROVIDERS), "embedder": list(BUNDLED_EMBEDDER_PROVIDERS)}


@app.post("/configure", summary="Configure Mem0")
def set_config(config: Dict[str, Any], _auth=Depends(require_admin)):
    """Set memory configuration. Requires admin role."""
    _validate_bundled_providers(config)
    update_config(config)
    return {"message": "Configuration set successfully"}


@app.post("/generate-instructions", summary="Generate custom instructions from a use case")
def generate_instructions(req: GenerateInstructionsRequest, _auth=Depends(verify_auth)):
    """Generate custom instructions and a contextual test message tailored to a use case."""
    try:
        llm = get_memory_instance().llm
        prompt = (
            "You are configuring a memory system. Given the use case below, produce two things:\n"
            "1. INSTRUCTIONS: A short paragraph of custom instructions telling the memory extraction system "
            "what kinds of facts, preferences, and context to prioritize. Be specific to the use case.\n"
            "2. TEST_MESSAGE: A single realistic sentence a user in this use case would say, suitable for "
            "testing that the memory system works.\n\n"
            "Respond in exactly this format (no markdown, no extra text):\n"
            "INSTRUCTIONS: <your instructions>\n"
            f"TEST_MESSAGE: <your test message>\n\nUse case: {req.use_case}"
        )
        response = llm.generate_response([{"role": "user", "content": prompt}])
        instructions = response
        test_message = "I like to hike on weekends."
        if "INSTRUCTIONS:" in response and "TEST_MESSAGE:" in response:
            parts = response.split("TEST_MESSAGE:")
            instructions = parts[0].replace("INSTRUCTIONS:", "").strip()
            test_message = parts[1].strip()
        return {"custom_instructions": instructions, "test_message": test_message}
    except Exception:
        raise upstream_error()


@app.post("/memories", summary="Create memories")
def add_memory(memory_create: MemoryCreate, principal: MemoryPrincipal = Depends(require_memory_principal)):
    """Store new memories."""
    reject_client_owner({"user_id": memory_create.user_id} if memory_create.user_id is not None else None)
    reject_client_owner(memory_create.metadata)
    app_id = _validated_app_id(memory_create.app_id)

    request_catalog = None
    if memory_create.custom_categories is not None:
        try:
            request_catalog = parse_per_call_categories(memory_create.custom_categories)
        except (PydanticValidationError, ValueError):
            raise HTTPException(status_code=422, detail="Invalid custom categories.")

    params = {
        key: value
        for key, value in memory_create.model_dump().items()
        if value is not None and key not in {"messages", "custom_categories", "user_id"}
    }
    params["user_id"] = principal.owner_id
    if app_id is not None:
        params["app_id"] = app_id
    origin_token = str(uuid.uuid4())
    params["metadata"] = {**(params.get("metadata") or {}), CATEGORY_ORIGIN_KEY: origin_token}
    try:
        service = get_category_service()
        catalog = service.resolve_catalog(principal.owner_id, request_catalog)
        with service.owner_fence(principal.owner_id):
            response = promote_category_fields(
                get_memory_instance().add(
                    messages=[message.model_dump() for message in memory_create.messages], **params
                )
            )
            try:
                response = service.after_add(
                    response,
                    catalog,
                    origin_token=origin_token,
                )
            except Exception:
                logging.warning("category_after_add_failed error_code=enqueue_failed")
        if response.get("results"):
            telemetry.log_dashboard_nudge_once(DASHBOARD_URL)
        return JSONResponse(content=response)
    except (ValueError, Mem0ValidationError) as e:
        raise _client_error(e)
    except Exception:
        raise upstream_error()


ALL_MEMORIES_LIMIT = 1000
_RESERVED_PAYLOAD_KEYS = {
    "data",
    "user_id",
    "agent_id",
    "run_id",
    "app_id",
    "hash",
    "created_at",
    "updated_at",
    "expiration_date",
    "categories",
    "category_status",
    "_category_generation",
    "_category_origin",
}


def _serialize_memory(row: Any) -> Dict[str, Any]:
    payload = getattr(row, "payload", None) or {}
    return {
        "id": getattr(row, "id", None),
        "memory": payload.get("data"),
        "user_id": payload.get("user_id"),
        "agent_id": payload.get("agent_id"),
        "run_id": payload.get("run_id"),
        "app_id": payload.get("app_id"),
        "hash": payload.get("hash"),
        "expiration_date": payload.get("expiration_date"),
        "categories": payload.get("categories"),
        "category_status": payload.get("category_status", "unclassified"),
        "metadata": {k: v for k, v in payload.items() if k not in _RESERVED_PAYLOAD_KEYS},
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
    }


@app.get("/memories", summary="Get memories")
def get_all_memories(
    user_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    app_id: Optional[str] = None,
    categories: Optional[List[str]] = Query(None),
    top_k: Optional[int] = Query(None, ge=0, le=ALL_MEMORIES_LIMIT),
    show_expired: bool = Query(False),
    principal: MemoryPrincipal = Depends(require_memory_principal),
):
    """Retrieve memories belonging to the authenticated account."""
    try:
        reject_client_owner({"user_id": user_id} if user_id is not None else None)
        extra = {}
        if categories is not None:
            extra["categories"] = {"in": categories}
        filters = owner_filters(
            principal,
            agent_id=agent_id,
            run_id=run_id,
            app_id=_validated_app_id(app_id),
            extra=extra,
        )
        params = {"filters": filters}
        if top_k is not None:
            params["top_k"] = top_k
        params["show_expired"] = show_expired
        return promote_category_fields(get_memory_instance().get_all(**params))
    except HTTPException:
        raise
    except Exception:
        raise upstream_error()


@app.get("/memories/{memory_id}", summary="Get a memory")
def get_memory(memory_id: str, principal: MemoryPrincipal = Depends(require_memory_principal)):
    """Retrieve a specific memory by ID."""
    try:
        memory = get_memory_instance()
        require_owned_memory(memory_id, principal, memory)
        return promote_category_fields(memory.get(memory_id))
    except HTTPException:
        raise
    except Exception:
        raise upstream_error()


@app.post("/search", summary="Search memories")
def search_memories(search_req: SearchRequest, principal: MemoryPrincipal = Depends(require_memory_principal)):
    """Search for memories based on a query."""
    try:
        reject_client_owner({"user_id": search_req.user_id} if search_req.user_id is not None else None)
        filters = owner_filters(
            principal,
            agent_id=search_req.agent_id,
            run_id=search_req.run_id,
            app_id=_validated_app_id(search_req.app_id),
            extra=search_req.filters or {},
        )
        params = {}
        if search_req.top_k is not None:
            params["top_k"] = search_req.top_k
        if search_req.threshold is not None:
            params["threshold"] = search_req.threshold
        if search_req.explain is not None:
            params["explain"] = search_req.explain
        if search_req.show_expired is not None:
            params["show_expired"] = search_req.show_expired
        return promote_category_fields(get_memory_instance().search(query=search_req.query, filters=filters, **params))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        raise upstream_error()


@app.put("/memories/{memory_id}", summary="Update a memory")
def update_memory(
    memory_id: str,
    updated_memory: MemoryUpdate,
    principal: MemoryPrincipal = Depends(require_memory_principal),
):
    """Update an existing memory."""
    try:
        reject_client_owner(updated_memory.metadata)
        fields_set = updated_memory.model_fields_set
        memory = get_memory_instance()
        require_owned_memory(memory_id, principal, memory)
        params = {"memory_id": memory_id}
        if "text" in fields_set:
            params["data"] = updated_memory.text
        if "metadata" in fields_set:
            params["metadata"] = updated_memory.metadata
        if "expiration_date" in fields_set:
            params["expiration_date"] = updated_memory.expiration_date
        service = get_category_service()
        update_kwargs = {"supplied_text": updated_memory.text} if "text" in fields_set else {}
        response = service.run_memory_update(
            memory_id,
            lambda: memory.update(**params),
            owner_id=principal.owner_id,
            **update_kwargs,
        )
        return promote_category_fields(response)
    except HTTPException:
        raise
    except (ValueError, Mem0ValidationError) as e:
        raise _client_error(e)
    except Exception:
        raise upstream_error()


@app.get("/memories/{memory_id}/history", summary="Get memory history")
def memory_history(memory_id: str, principal: MemoryPrincipal = Depends(require_memory_principal)):
    """Retrieve memory history."""
    try:
        memory = get_memory_instance()
        require_owned_memory(memory_id, principal, memory)
        return memory.history(memory_id=memory_id)
    except HTTPException:
        raise
    except Exception:
        raise upstream_error()


@app.delete("/memories/{memory_id}", summary="Delete a memory", response_model=MessageResponse)
def delete_memory(memory_id: str, principal: MemoryPrincipal = Depends(require_memory_principal)):
    """Delete a specific memory by ID."""
    try:
        memory = get_memory_instance()
        require_owned_memory(memory_id, principal, memory)
        memory.delete(memory_id=memory_id)
        try:
            get_category_service().after_delete(memory_id, principal.owner_id)
        except Exception:
            logging.warning("category_after_delete_failed memory_id=%s error_code=cancel_failed", memory_id)
        return MessageResponse(message="Memory deleted successfully")
    except HTTPException:
        raise
    except (ValueError, Mem0ValidationError) as e:
        raise _client_error(e)
    except Exception:
        raise upstream_error()


@app.delete("/memories", summary="Delete all memories", response_model=MessageResponse)
def delete_all_memories(
    user_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    app_id: Optional[str] = None,
    principal: MemoryPrincipal = Depends(require_memory_principal),
):
    """Delete this account's memories, optionally scoped by app, agent, or run."""
    try:
        reject_client_owner({"user_id": user_id} if user_id is not None else None)
        params = {
            "user_id": principal.owner_id,
            "agent_id": agent_id,
            "run_id": run_id,
        }
        if (validated_app_id := _validated_app_id(app_id)) is not None:
            params["app_id"] = validated_app_id
        get_memory_instance().delete_all(**params)
        return MessageResponse(message="All relevant memories deleted")
    except HTTPException:
        raise
    except Exception:
        raise upstream_error()


@app.post("/reset", summary="Reset all memories")
def reset_memory(principal: MemoryPrincipal = Depends(require_memory_principal)):
    """Delete every memory owned by the authenticated account."""
    try:
        service = get_category_service()
        with service.owner_fence(principal.owner_id):
            get_memory_instance().delete_all(user_id=principal.owner_id)
            try:
                cleaned = service.after_owner_reset(principal.owner_id)
            except Exception:
                logging.warning("category_after_owner_reset_failed error_code=purge_failed")
                raise HTTPException(status_code=503, detail="Reset cleanup incomplete.") from None
            if not cleaned:
                logging.warning("category_after_owner_reset_failed error_code=purge_failed")
                raise HTTPException(status_code=503, detail="Reset cleanup incomplete.")
        return {"message": "All memories reset"}
    except HTTPException:
        raise
    except Exception:
        raise upstream_error()


@app.get("/", summary="Redirect to the OpenAPI documentation", include_in_schema=False)
def home():
    """Redirect to the OpenAPI documentation."""
    return RedirectResponse(url="/docs")
