"""Authenticated REST routes for custom category administration."""

import math
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from category_job_errors import public_error_code
from category_models import CategoryDefinition, CategoryJobState
from category_runtime import get_category_service
from category_service import CatalogView, CategoryService, ReclassificationPreview, ReclassificationStart
from memory_authorization import MemoryPrincipal, require_memory_principal


class CategoryCount(BaseModel):
    """A durable historical category label and its current memory count."""

    name: str
    count: int = Field(ge=0)


router = APIRouter(prefix="/categories", tags=["categories"])


class CatalogResponse(BaseModel):
    """Saved and currently active categories remain distinct at the REST boundary."""

    saved: list[CategoryDefinition]
    active: list[CategoryDefinition]
    source: Literal["defaults", "user"]
    counts: dict[str, int]
    retired: list[CategoryCount]


class CategoryPatchRequest(BaseModel):
    """A non-empty partial category update, including an explicit rename when supplied."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    description: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_change(self) -> "CategoryPatchRequest":
        if self.name is None and self.description is None:
            raise ValueError("At least one category field is required.")
        return self


class ReclassificationPreviewRequest(BaseModel):
    """Optional operator supplied rate pair used for a non-persisting estimate."""

    model_config = ConfigDict(extra="forbid")

    scope: Literal["unclassified_failed", "all"] = "unclassified_failed"
    input_rate_per_million: float | None = Field(default=None, ge=0)
    output_rate_per_million: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_rate_pair(self) -> "ReclassificationPreviewRequest":
        if (self.input_rate_per_million is None) != (self.output_rate_per_million is None):
            raise ValueError("Input and output rates must be supplied together.")
        for value in (self.input_rate_per_million, self.output_rate_per_million):
            if value is not None and not math.isfinite(value):
                raise ValueError("Rates must be finite.")
        return self


class ReclassificationStartRequest(BaseModel):
    """A deliberately exact confirmation prevents accidental historical work."""

    model_config = ConfigDict(extra="forbid")

    scope: Literal["unclassified_failed", "all"] = "unclassified_failed"
    confirm: Literal["RECLASSIFY"]


class ReclassificationPreviewResponse(BaseModel):
    scope: Literal["unclassified_failed", "all"]
    eligible_memories: int
    estimated_calls: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost: float | None


class ReclassificationStartResponse(BaseModel):
    created_jobs: int
    skipped_active_jobs: int
    eligible_memories: int


class CategoryJobResponse(BaseModel):
    """The public, operationally safe subset of a durable job."""

    id: str
    memory_id: str
    state: CategoryJobState
    attempts: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    next_attempt_at: datetime | None
    error_code: str | None
    error_message: str | None


def _catalog_response(view: CatalogView) -> CatalogResponse:
    active_names = {definition.name for definition in view.active}
    retired = [CategoryCount(name=name, count=count) for name, count in sorted(view.counts.items()) if name not in active_names]
    return CatalogResponse(
        saved=list(view.saved),
        active=list(view.active),
        source="defaults" if view.source == "defaults" else "user",
        counts=view.counts,
        retired=retired,
    )


def _preview_response(preview: ReclassificationPreview) -> ReclassificationPreviewResponse:
    return ReclassificationPreviewResponse(
        scope=preview.scope,
        eligible_memories=preview.eligible_memories,
        estimated_calls=preview.estimated_calls,
        estimated_input_tokens=preview.estimated_input_tokens,
        estimated_output_tokens=preview.estimated_output_tokens,
        estimated_cost=preview.estimated_cost,
    )


def _start_response(start: ReclassificationStart) -> ReclassificationStartResponse:
    return ReclassificationStartResponse(
        created_jobs=start.created_jobs,
        skipped_active_jobs=start.skipped_active_jobs,
        eligible_memories=start.eligible_memories,
    )


def _job_response(job: object) -> CategoryJobResponse:
    """Whitelist safe fields rather than serializing database rows directly."""
    return CategoryJobResponse(
        id=str(getattr(job, "id")),
        memory_id=str(getattr(job, "memory_id")),
        state=getattr(job, "state"),
        attempts=getattr(job, "attempts"),
        created_at=getattr(job, "created_at"),
        updated_at=getattr(job, "updated_at"),
        started_at=getattr(job, "started_at"),
        completed_at=getattr(job, "completed_at"),
        next_attempt_at=getattr(job, "next_attempt_at"),
        error_code=public_error_code(getattr(job, "error_code")),
        error_message=getattr(job, "error_message"),
    )


def _invalid_category_request() -> HTTPException:
    return HTTPException(status_code=400, detail="Invalid category request.")


@router.get("", response_model=CatalogResponse)
def get_categories(
    principal: MemoryPrincipal = Depends(require_memory_principal), service: CategoryService = Depends(get_category_service)
) -> CatalogResponse:
    return _catalog_response(service.get_catalog_view(principal.owner_id))


@router.post("", response_model=CatalogResponse, status_code=201)
def create_category(
    definition: CategoryDefinition,
    principal: MemoryPrincipal = Depends(require_memory_principal),
    service: CategoryService = Depends(get_category_service),
) -> CatalogResponse:
    try:
        return _catalog_response(service.create_category(definition, principal.owner_id))
    except ValueError:
        raise _invalid_category_request() from None


@router.put("", response_model=CatalogResponse)
def replace_categories(
    definitions: list[CategoryDefinition],
    principal: MemoryPrincipal = Depends(require_memory_principal),
    service: CategoryService = Depends(get_category_service),
) -> CatalogResponse:
    try:
        return _catalog_response(service.replace_catalog(tuple(definitions), principal.owner_id))
    except ValueError:
        raise _invalid_category_request() from None


@router.get("/jobs", response_model=list[CategoryJobResponse])
def list_category_jobs(
    state: list[CategoryJobState] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=100),
    principal: MemoryPrincipal = Depends(require_memory_principal),
    service: CategoryService = Depends(get_category_service),
) -> list[CategoryJobResponse]:
    jobs = service.list_jobs(owner_id=principal.owner_id, states=tuple(state) if state else None, limit=limit)
    return [_job_response(job) for job in jobs]


@router.post("/reclassify/preview", response_model=ReclassificationPreviewResponse)
def preview_reclassification(
    body: ReclassificationPreviewRequest,
    principal: MemoryPrincipal = Depends(require_memory_principal),
    service: CategoryService = Depends(get_category_service),
) -> ReclassificationPreviewResponse:
    try:
        return _preview_response(
            service.preview_reclassification(
                scope=body.scope,
                owner_id=principal.owner_id,
                input_rate_per_million=body.input_rate_per_million,
                output_rate_per_million=body.output_rate_per_million,
            )
        )
    except ValueError:
        raise _invalid_category_request() from None


@router.post("/reclassify", response_model=ReclassificationStartResponse, status_code=202)
def start_reclassification(
    body: ReclassificationStartRequest,
    principal: MemoryPrincipal = Depends(require_memory_principal),
    service: CategoryService = Depends(get_category_service),
) -> ReclassificationStartResponse:
    try:
        return _start_response(
            service.start_reclassification(scope=body.scope, confirm=body.confirm, owner_id=principal.owner_id)
        )
    except ValueError:
        raise _invalid_category_request() from None


@router.patch("/{name}", response_model=CatalogResponse)
def update_category(
    name: str,
    body: CategoryPatchRequest,
    principal: MemoryPrincipal = Depends(require_memory_principal),
    service: CategoryService = Depends(get_category_service),
) -> CatalogResponse:
    try:
        return _catalog_response(
            service.update_category(name, principal.owner_id, new_name=body.name, description=body.description)
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Category not found.") from None
    except ValueError:
        raise _invalid_category_request() from None


@router.delete("/{name}", response_model=CatalogResponse)
def delete_category(
    name: str,
    principal: MemoryPrincipal = Depends(require_memory_principal),
    service: CategoryService = Depends(get_category_service),
) -> CatalogResponse:
    try:
        return _catalog_response(service.delete_category(name, principal.owner_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="Category not found.") from None
    except ValueError:
        raise _invalid_category_request() from None
