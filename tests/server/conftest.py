"""Shared fixtures for server category contract tests."""

import os
import sys
from unittest.mock import MagicMock

import pytest

# server/ modules use bare imports (from auth import ...), so the server
# directory itself must be importable, mirroring how it runs in Docker.
_SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "server")
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)


@pytest.fixture
def session_factory():
    """Provide a database-free session factory for future route tests."""
    return MagicMock(name="session_factory")


@pytest.fixture
def vector_row():
    """Provide a representative vector-store row without PostgreSQL."""
    return MagicMock(name="vector_row", id="memory-1", payload={"data": "memory"})
