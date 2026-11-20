"""Utility functions shared by the route modules."""

import re

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def parse_int(value: str | None, default: int) -> int:
    """Parse a query-string integer, falling back to ``default``."""
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def paginate(items: list, page: int, per_page: int) -> list:
    """Return the slice of ``items`` for a 1-indexed ``page``."""
    start = page * per_page
    return items[start:start + per_page]


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))
