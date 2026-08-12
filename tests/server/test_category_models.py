"""Contract tests for self-hosted custom categories."""

import json
from types import MappingProxyType

import pytest
from pydantic import BaseModel, ValidationError

from category_models import (
    CategoryDefinition,
    CategoryJobState,
    EffectiveCatalog,
    default_catalog,
    parse_per_call_categories,
    promote_category_fields,
    validate_catalog,
)
from category_service import CatalogView, ReclassificationPreview, ReclassificationStart
from category_store import ClaimedCategoryJob, EnqueueResult, MemorySnapshot


def test_default_catalog_has_documented_order():
    assert [item.name for item in default_catalog()] == [
        "personal_details",
        "family",
        "professional_details",
        "sports",
        "travel",
        "food",
        "music",
        "health",
        "technology",
        "hobbies",
        "fashion",
        "entertainment",
        "milestones",
        "user_preferences",
        "misc",
    ]


def test_default_catalog_is_the_defaults_effective_catalog_fallback():
    catalog = EffectiveCatalog(definitions=default_catalog(), source="defaults")
    assert catalog.source == "defaults"
    assert catalog.definitions == default_catalog()


def test_category_domain_records_are_frozen_pydantic_models_with_tuple_snapshots():
    definition = CategoryDefinition(name="billing", description="Invoices")
    records = (
        EffectiveCatalog(definitions=(definition,), source="project"),
        CatalogView(saved=(definition,), active=(definition,), source="project", counts={"billing": 1}),
        ReclassificationPreview(
            scope="all",
            eligible_memories=1,
            estimated_calls=1,
            estimated_input_tokens=2,
            estimated_output_tokens=1,
            estimated_cost=None,
        ),
        ReclassificationStart(created_jobs=1, skipped_active_jobs=0, eligible_memories=1),
        EnqueueResult(job_id="11111111-1111-1111-1111-111111111111", created=True),
        ClaimedCategoryJob(
            id="11111111-1111-1111-1111-111111111111",
            memory_id="m1",
            owner_id="00000000-0000-0000-0000-000000000001",
            memory_hash="h1",
            catalog=(definition,),
            attempts=1,
        ),
        MemorySnapshot(
            memory_id="m1",
            user_id="00000000-0000-0000-0000-000000000001",
            text="Invoice",
            memory_hash="h1",
            categories=("billing",),
            category_status="completed",
            payload=MappingProxyType({"data": "Invoice", "metadata": MappingProxyType({"priority": 1})}),
        ),
    )

    assert all(isinstance(record, BaseModel) for record in records)
    assert records[0].model_dump()["definitions"] == ({"name": "billing", "description": "Invoices"},)
    assert records[-1].categories == ("billing",)
    assert json.loads(records[-1].model_dump_json())["payload"] == {
        "data": "Invoice",
        "metadata": {"priority": 1},
    }
    with pytest.raises(ValidationError, match="Instance is frozen"):
        records[-1].text = "Changed"


@pytest.mark.parametrize("owner", [None, "", "not-a-uuid"])
def test_memory_snapshot_requires_a_valid_owner_uuid(owner):
    values = {
        "memory_id": "m1",
        "text": "Invoice",
        "memory_hash": "h1",
        "categories": None,
        "category_status": "unclassified",
        "payload": {},
    }
    if owner is not None:
        values["user_id"] = owner

    with pytest.raises(ValidationError):
        MemorySnapshot(**values)


def test_category_definition_strips_whitespace_and_is_frozen_with_no_extra_fields():
    category = CategoryDefinition(name=" billing ", description=" Invoices and payments ")
    assert category == CategoryDefinition(name="billing", description="Invoices and payments")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CategoryDefinition(name="billing", description="Invoices", color="blue")
    with pytest.raises(ValidationError, match="Instance is frozen"):
        category.name = "health"


@pytest.mark.parametrize("name", ["Billing", "billing-name", "billing name", "1billing", "billing!"])
def test_category_definition_rejects_invalid_names(name):
    with pytest.raises(ValidationError):
        CategoryDefinition(name=name, description="Invoices and payments")


def test_category_definition_accepts_64_character_name_and_rejects_a_longer_name():
    assert CategoryDefinition(name="a" * 64, description="Valid description").name == "a" * 64
    with pytest.raises(ValidationError):
        CategoryDefinition(name="a" * 65, description="Valid description")


def test_category_definition_accepts_500_character_description_and_rejects_blank_or_longer_description():
    assert CategoryDefinition(name="billing", description="x" * 500).description == "x" * 500
    with pytest.raises(ValidationError):
        CategoryDefinition(name="billing", description=" " * 3)
    with pytest.raises(ValidationError):
        CategoryDefinition(name="billing", description="x" * 501)


def test_validate_catalog_accepts_up_to_50_unique_definitions():
    definitions = tuple(CategoryDefinition(name=f"category_{index}", description="Valid") for index in range(50))
    assert validate_catalog(definitions) == definitions


def test_validate_catalog_rejects_more_than_50_definitions():
    definitions = tuple(CategoryDefinition(name=f"category_{index}", description="Valid") for index in range(51))
    with pytest.raises(ValueError, match="at most 50"):
        validate_catalog(definitions)


def test_validate_catalog_rejects_duplicate_names():
    definitions = (
        CategoryDefinition(name="billing", description="Invoices"),
        CategoryDefinition(name="billing", description="Payments"),
    )
    with pytest.raises(ValueError, match="unique"):
        validate_catalog(definitions)


def test_per_call_catalog_requires_one_key_objects():
    parsed = parse_per_call_categories([{"billing": "Invoices and payments"}])
    assert parsed == (CategoryDefinition(name="billing", description="Invoices and payments"),)
    with pytest.raises(ValueError, match="exactly one"):
        parse_per_call_categories([{"billing": "Bills", "health": "Care"}])
    with pytest.raises(ValueError, match="must not be empty"):
        parse_per_call_categories([])


def test_per_call_catalog_rejects_non_mapping_entries():
    with pytest.raises(ValueError, match="exactly one"):
        parse_per_call_categories(["billing"])


def test_job_states_have_stable_wire_values():
    assert {state.value for state in CategoryJobState} == {
        "queued",
        "processing",
        "retrying",
        "completed",
        "failed",
        "cancelled",
    }


def test_promote_category_fields_handles_legacy_and_nested_results():
    value = {"results": [{"id": "old", "memory": "x", "metadata": {"source": "a"}}]}
    assert promote_category_fields(value)["results"][0] == {
        "id": "old",
        "memory": "x",
        "metadata": {"source": "a"},
        "categories": None,
        "category_status": "unclassified",
    }


def test_promote_category_fields_moves_metadata_fields_without_mutating_input():
    value = {
        "id": "new",
        "memory": "x",
        "metadata": {"categories": ["billing"], "category_status": "classified", "source": "api"},
    }
    promoted = promote_category_fields(value)
    assert promoted == {
        "id": "new",
        "memory": "x",
        "metadata": {"source": "api"},
        "categories": ["billing"],
        "category_status": "classified",
    }
    assert value["metadata"] == {"categories": ["billing"], "category_status": "classified", "source": "api"}


def test_promote_category_fields_hides_internal_origin_and_generation_tokens():
    promoted = promote_category_fields(
        {
            "id": "m1",
            "memory": "x",
            "metadata": {
                "_category_origin": "request-token",
                "_category_generation": "job-token",
                "source": "api",
            },
        }
    )

    assert promoted["metadata"] == {"source": "api"}
    assert "_category_origin" not in promoted
    assert "_category_generation" not in promoted


def test_promote_category_fields_strips_internal_generation_from_memory_metadata():
    value = {
        "id": "new",
        "memory": "x",
        "metadata": {"_category_generation": "secret-job-token", "source": "api"},
    }

    promoted = promote_category_fields(value)

    assert promoted["metadata"] == {"source": "api"}
    assert "_category_generation" not in promoted


def test_promote_category_fields_defaults_memory_without_metadata():
    value = {"id": "no-metadata", "memory": "x"}
    assert promote_category_fields(value) == {
        "id": "no-metadata",
        "memory": "x",
        "categories": None,
        "category_status": "unclassified",
    }


def test_promote_category_fields_defaults_memory_with_none_metadata():
    value = {"id": "none-metadata", "memory": "x", "metadata": None}
    assert promote_category_fields(value) == {
        "id": "none-metadata",
        "memory": "x",
        "metadata": None,
        "categories": None,
        "category_status": "unclassified",
    }


def test_promote_category_fields_defaults_only_the_missing_top_level_category_field():
    value = {"id": "top-level", "memory": "x", "categories": ["billing"]}
    assert promote_category_fields(value) == {
        "id": "top-level",
        "memory": "x",
        "categories": ["billing"],
        "category_status": "unclassified",
    }


def test_promote_category_fields_leaves_non_memory_envelopes_and_metadata_dictionaries_unchanged():
    value = {"results": [], "metadata": {"categories": ["billing"], "category_status": "classified"}}
    assert promote_category_fields(value) == value


def test_promote_category_fields_preserves_metadata_containing_an_id():
    value = {"id": "memory-1", "memory": "x", "metadata": {"source": {"id": "source-1"}}}
    assert promote_category_fields(value) == {
        "id": "memory-1",
        "memory": "x",
        "metadata": {"source": {"id": "source-1"}},
        "categories": None,
        "category_status": "unclassified",
    }


def test_promote_category_fields_leaves_id_bearing_non_memory_envelopes_unchanged():
    value = {"id": "request-1", "results": []}
    assert promote_category_fields(value) == value


def test_promote_category_fields_recurses_through_lists():
    promoted = promote_category_fields([
        {"id": "one", "memory": "x", "metadata": {"categories": ["travel"]}},
        {"id": "two", "memory": "x", "metadata": {"category_status": "failed"}},
    ])
    assert promoted == [
        {"id": "one", "memory": "x", "metadata": {}, "categories": ["travel"], "category_status": "unclassified"},
        {"id": "two", "memory": "x", "metadata": {}, "categories": None, "category_status": "failed"},
    ]
