# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: Apache-2.0

import pytest

from app_scope import AppScope, read_filters, validate_app_id, write_app_id


@pytest.mark.parametrize("value", ["github.com-olhapi-ram0", "git.home.olhapi.com-oleh-bet", "local-repo_2"])
def test_validate_app_id_accepts_normalized_git_ids(value):
    """Rejecting normalized Git identifiers makes trusted project scope unusable."""
    assert validate_app_id(value) == value


@pytest.mark.parametrize("value", ["", " ", "/Users/oleh/ram0", "../ram0", "a" * 129])
def test_validate_app_id_rejects_unsafe_or_ambiguous_values(value):
    """Accepting raw paths or invalid identifiers permits ambiguous project scopes."""
    with pytest.raises(ValueError, match="app_id"):
        validate_app_id(value)


def test_default_read_is_owner_and_project_or_global():
    """Dropping either read branch hides account-global or project-local memory."""
    assert read_filters("owner-a", "github.com-olhapi-ram0", None) == {
        "AND": [
            {"user_id": "owner-a"},
            {"OR": [{"app_id": "github.com-olhapi-ram0"}, {"app_id": None}]},
        ]
    }


def test_explicit_scopes_are_exact():
    """Broadening explicit project or global choices crosses the selected scope boundary."""
    assert read_filters("owner-a", "app-a", AppScope.PROJECT) == {
        "AND": [{"user_id": "owner-a"}, {"app_id": "app-a"}]
    }
    assert read_filters("owner-a", None, AppScope.GLOBAL) == {"user_id": "owner-a"}
    assert write_app_id("app-a", None) == "app-a"
    assert write_app_id("app-a", AppScope.PROJECT) == "app-a"
    assert write_app_id(None, AppScope.GLOBAL) is None


def test_project_context_is_required_and_global_rejects_app_id():
    """Allowing ambiguous writes lets callers choose a scope outside the trusted project context."""
    with pytest.raises(ValueError, match="project context"):
        read_filters("owner-a", None, None)
    with pytest.raises(ValueError, match="global"):
        write_app_id("app-a", AppScope.GLOBAL)
