"""Pure contract types and helpers for self-hosted memory categories."""

from enum import Enum
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field


DEFAULT_CATEGORY_DESCRIPTIONS = {
    "personal_details": "Identity, age, location, education, and personal background.",
    "family": "Family members, relationships, household, and family events.",
    "professional_details": "Employment, career, workplace, skills, and professional goals.",
    "sports": "Sports played, followed, watched, or preferred.",
    "travel": "Trips, destinations, travel plans, and travel preferences.",
    "food": "Food, cooking, restaurants, diets, and dining preferences.",
    "music": "Artists, genres, instruments, concerts, and listening preferences.",
    "health": "Health conditions, care, wellness, fitness, and medical information.",
    "technology": "Devices, software, technical interests, and technology preferences.",
    "hobbies": "Leisure activities, crafts, collections, and recurring interests.",
    "fashion": "Clothing, style, accessories, sizes, and fashion preferences.",
    "entertainment": "Films, television, books, games, and other media preferences.",
    "milestones": "Important achievements, anniversaries, transitions, and life events.",
    "user_preferences": "General likes, dislikes, habits, choices, and preferred behavior.",
    "misc": "Useful personal context that does not fit another active category.",
}
CATEGORY_GENERATION_KEY = "_category_generation"
CATEGORY_ORIGIN_KEY = "_category_origin"


class CategoryDefinition(BaseModel):
    """An immutable category that can be exposed to a classifier."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1, max_length=500)


class EffectiveCatalog(BaseModel):
    """The validated category definitions selected for an operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    definitions: tuple[CategoryDefinition, ...]
    source: Literal["defaults", "project", "request"]


class CategoryJobState(str, Enum):
    """Stable states for asynchronous category work."""

    QUEUED = "queued"
    PROCESSING = "processing"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def validate_catalog(definitions: tuple[CategoryDefinition, ...] | list[CategoryDefinition]) -> tuple[CategoryDefinition, ...]:
    """Return a bounded catalogue with unique category names."""
    catalog = tuple(definitions)
    if len(catalog) > 50:
        raise ValueError("A category catalog may contain at most 50 definitions.")
    if len({definition.name for definition in catalog}) != len(catalog):
        raise ValueError("Category names must be unique.")
    return catalog


def default_catalog() -> tuple[CategoryDefinition, ...]:
    """Return the server-owned default categories in their documented order."""
    return tuple(
        CategoryDefinition(name=name, description=description) for name, description in DEFAULT_CATEGORY_DESCRIPTIONS.items()
    )


def parse_per_call_categories(value: Any) -> tuple[CategoryDefinition, ...]:
    """Parse request categories represented as one-key name-to-description objects."""
    if not isinstance(value, list) or not value:
        raise ValueError("Per-call categories must not be empty.")

    definitions = []
    for item in value:
        if not isinstance(item, Mapping) or len(item) != 1:
            raise ValueError("Each per-call category must be an object with exactly one key.")
        name, description = next(iter(item.items()))
        definitions.append(CategoryDefinition(name=name, description=description))
    return validate_catalog(definitions)


def promote_category_fields(value: Any) -> Any:
    """Move legacy category fields from memory metadata to the memory object."""
    if isinstance(value, list):
        return [promote_category_fields(item) for item in value]
    if not isinstance(value, dict):
        return value

    promoted = {key: promote_category_fields(item) for key, item in value.items()}
    if not {"id", "memory"}.issubset(promoted):
        return promoted

    metadata = promoted.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop(CATEGORY_GENERATION_KEY, None)
        metadata.pop(CATEGORY_ORIGIN_KEY, None)
        categories = metadata.pop("categories", promoted.get("categories", None))
        category_status = metadata.pop("category_status", promoted.get("category_status", "unclassified"))
    else:
        categories = promoted.get("categories", None)
        category_status = promoted.get("category_status", "unclassified")
    promoted["categories"] = categories
    promoted["category_status"] = category_status
    promoted.pop(CATEGORY_GENERATION_KEY, None)
    promoted.pop(CATEGORY_ORIGIN_KEY, None)
    return promoted
