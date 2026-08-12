"""Contracts for internal category-job error markers and their REST-safe projection."""

import pytest

from category_job_errors import (
    parse_terminal_error,
    public_error_code,
    safe_error_message,
    sanitize_error_code,
    terminal_marker,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("provider error!", "provider_error"),
        ("invalid_json", "invalid_json"),
        ("raw secret text", "category_error"),
        ("memory_deleted", "memory_deleted"),
    ],
)
def test_error_codes_are_reduced_to_the_shared_allowlist(value, expected):
    assert sanitize_error_code(value) == expected


def test_terminal_marker_round_trips_safe_code_and_retry_count():
    marker = terminal_marker("provider_error", 4)

    assert marker == "_terminalizing_4_provider_error"
    assert parse_terminal_error(marker) == ("provider_error", 4, False)
    assert public_error_code(marker) == "provider_error"
    assert safe_error_message("provider_error") == "Category provider request failed"


@pytest.mark.parametrize(
    "marker",
    [
        "_terminalizing_²_invalid_json",
        f"_terminalizing_{'9' * 40}_invalid_json",
        "_terminalizing_2_raw_secret",
    ],
)
def test_malformed_terminal_markers_never_convert_or_escape_publicly(marker):
    assert parse_terminal_error(marker) == ("category_error", 0, True)
    assert public_error_code(marker) == "category_error"


def test_legacy_terminal_marker_remains_readable():
    assert parse_terminal_error("_terminalizing_invalid_json") == (
        "invalid_json",
        0,
        False,
    )
    assert public_error_code("_terminalizing_invalid_json") == "invalid_json"


def test_public_error_projection_preserves_none_and_safe_cancellation_reasons():
    assert public_error_code(None) is None
    assert public_error_code("replaced") == "replaced"
    assert public_error_code("untrusted database text") == "category_error"
