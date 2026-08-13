# Modified for Ram0; see NOTICE and repository history.
"""Unit contracts for the immutable core-memory ownership policy."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from memory_authorization import MemoryPrincipal, owner_filters, reject_client_owner, require_owned_memory


@pytest.fixture
def principal() -> MemoryPrincipal:
    return MemoryPrincipal(owner_id="00000000-0000-0000-0000-000000000001")


def vector_row(*, user_id: str):
    return SimpleNamespace(id="memory-id", payload={"user_id": user_id, "data": "private"})


def test_owner_filters_can_be_narrowed_by_a_trusted_app_id(principal: MemoryPrincipal):
    """Dropping the trusted app clause permits a project-specific query to read account-wide memory."""
    assert owner_filters(
        principal, agent_id="a", app_id="github.com-olhapi-ram0", extra={"categories": {"in": ["work"]}}
    ) == {
        "user_id": principal.owner_id,
        "agent_id": "a",
        "app_id": "github.com-olhapi-ram0",
        "categories": {"in": ["work"]},
    }


@pytest.mark.parametrize(
    "value",
    [
        {"user_id": "other"},
        {"AND": [{"agent_id": "a"}, {"user_id": {"in": ["other"]}}]},
        {"app_id": "other-project"},
        {"AND": [{"agent_id": "a"}, {"app_id": {"in": ["other-project"]}}]},
    ],
)
def test_nested_owner_selectors_are_rejected(value: object):
    """Removing recursive validation lets nested client predicates bypass trusted isolation."""
    with pytest.raises(HTTPException) as error:
        reject_client_owner(value)

    assert error.value.status_code == 422


def test_owner_filters_validates_the_trusted_app_id(principal: MemoryPrincipal):
    """Skipping validation allows the server-side project argument to become an unsafe filter."""
    with pytest.raises(ValueError, match="app_id"):
        owner_filters(principal, app_id="../other-project")


def test_excessively_nested_client_structure_is_rejected_deterministically():
    """Removing the depth guard lets parsable attacker input exhaust Python recursion and become a 500."""
    value: object = {}
    for _ in range(66):
        value = {"AND": [value]}

    with pytest.raises(HTTPException) as error:
        reject_client_owner(value)

    assert (error.value.status_code, error.value.detail) == (422, "Request structure is too deeply nested.")


def test_missing_and_foreign_memory_are_indistinguishable(principal: MemoryPrincipal):
    """Changing either branch exposes whether a guessed memory ID belongs to someone else."""
    memory = MagicMock()
    for row in (None, vector_row(user_id="00000000-0000-0000-0000-000000000099")):
        memory.vector_store.get.return_value = row

        with pytest.raises(HTTPException) as error:
            require_owned_memory("memory-id", principal, memory)

        assert (error.value.status_code, error.value.detail) == (404, "Memory not found.")


def test_owned_memory_is_returned_for_the_authenticated_account(principal: MemoryPrincipal):
    """Rejecting the matching owner would make a caller unable to use its own memory."""
    row = vector_row(user_id=principal.owner_id)
    memory = MagicMock()
    memory.vector_store.get.return_value = row

    assert require_owned_memory("memory-id", principal, memory) is row
