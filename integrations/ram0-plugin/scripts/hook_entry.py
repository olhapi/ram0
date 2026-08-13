#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: Apache-2.0
"""Project context projection for Ram0 lifecycle hook events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from project_scope import resolve_project_context


@dataclass(frozen=True, slots=True)
class HookProjectContext:
    """The only project value allowed to leave the local resolver seam."""

    app_id: str


def resolve_hook_project(event: Any) -> HookProjectContext:
    """Resolve fresh context from this event's cwd without exposing raw host fields."""
    event_cwd = event.get("cwd") if isinstance(event, dict) and isinstance(event.get("cwd"), str) else None
    return HookProjectContext(app_id=resolve_project_context(event_cwd).app_id)


def project_scope_context(context: HookProjectContext) -> str:
    """Expose only the normalized label an agent must supply to scoped MCP calls."""
    return (
        "<ram0-project-context>\n"
        f"Current Ram0 app_id: {context.app_id}\n"
        "Use this value only as the top-level app_id for default or project-scoped Ram0 tools. "
        "Never place app_id in metadata.\n"
        "</ram0-project-context>"
    )
