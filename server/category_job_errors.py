"""One safe contract for durable category-job error codes and recovery markers."""

import re


TERMINALIZING_PREFIX = "_terminalizing_"
MAX_TERMINAL_RETRY_DIGITS = 9
SAFE_ERROR_MESSAGES = {
    "category_error": "Category classification failed",
    "invalid_json": "Invalid category response",
    "provider_error": "Category provider request failed",
}
SAFE_REASON_CODES = frozenset((*SAFE_ERROR_MESSAGES, "memory_deleted", "replaced", "reset"))
_SAFE_CODE = re.compile(r"[^a-z0-9]+")


def sanitize_error_code(value: str) -> str:
    """Reduce arbitrary internal/provider text to one durable allowlisted code."""
    code = _SAFE_CODE.sub("_", value.lower()).strip("_")[:64]
    return code if code in SAFE_REASON_CODES else "category_error"


def safe_error_message(code: str) -> str:
    """Return the operator-safe message for a classifier error code."""
    return SAFE_ERROR_MESSAGES.get(code, SAFE_ERROR_MESSAGES["category_error"])


def parse_terminal_error(code: str | None) -> tuple[str, int, bool] | None:
    """Parse an internal recovery marker as ``(safe code, retry count, malformed)``."""
    if not code or not code.startswith(TERMINALIZING_PREFIX):
        return None
    encoded = code.removeprefix(TERMINALIZING_PREFIX)
    if encoded in SAFE_ERROR_MESSAGES:
        return encoded, 0, False
    retry_text, separator, original = encoded.partition("_")
    valid_count = bool(
        separator
        and retry_text.isascii()
        and retry_text.isdecimal()
        and len(retry_text) <= MAX_TERMINAL_RETRY_DIGITS
    )
    if not valid_count or original not in SAFE_ERROR_MESSAGES:
        return "category_error", 0, True
    return original, int(retry_text), False


def terminal_marker(code: str, retries: int) -> str:
    """Encode internal failed-payload recovery state without a public job state."""
    safe_code = sanitize_error_code(code)
    if safe_code not in SAFE_ERROR_MESSAGES:
        safe_code = "category_error"
    return f"{TERMINALIZING_PREFIX}{max(retries, 0)}_{safe_code}"


def public_error_code(code: str | None) -> str | None:
    """Project stored error data to a REST-safe code with no internal marker details."""
    if code is None:
        return None
    terminal = parse_terminal_error(code)
    if terminal is not None:
        safe_code, _retries, malformed = terminal
        return "category_error" if malformed else safe_code
    return sanitize_error_code(code)
