# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: Apache-2.0
# Modified for Ram0; see NOTICE and repository history.
"""Account-local project scope policy for memory operations."""

from enum import Enum
import re
from typing import Any


APP_ID_MAX_LENGTH = 128
_APP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class AppScope(str, Enum):
    """Explicit client choice for a trusted project context."""

    PROJECT = "project"
    GLOBAL = "global"


def validate_app_id(value: str | None) -> str:
    """Accept one normalized Git-project identifier, never a caller path."""
    if not isinstance(value, str) or len(value) > APP_ID_MAX_LENGTH or not _APP_ID.fullmatch(value):
        raise ValueError("app_id must be a non-empty normalized project identifier.")
    return value


def read_filters(owner_id: str, app_id: str | None, scope: AppScope | None) -> dict[str, Any]:
    """Build account-owned filters for default, project, or global reads."""
    if scope is AppScope.GLOBAL:
        if app_id is not None:
            raise ValueError("global scope does not accept app_id.")
        return {"user_id": owner_id}
    if app_id is None:
        raise ValueError("project context is required.")
    current = validate_app_id(app_id)
    if scope is AppScope.PROJECT:
        return {"AND": [{"user_id": owner_id}, {"app_id": current}]}
    return {"AND": [{"user_id": owner_id}, {"OR": [{"app_id": current}, {"app_id": None}]}]}


def write_app_id(app_id: str | None, scope: AppScope | None) -> str | None:
    """Return the scoped app identifier to persist for a memory write."""
    if scope is AppScope.GLOBAL:
        if app_id is not None:
            raise ValueError("global scope does not accept app_id.")
        return None
    if app_id is None:
        raise ValueError("project context is required.")
    return validate_app_id(app_id)
