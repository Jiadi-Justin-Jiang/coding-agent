"""Apply search/replace edits produced by the model.

Search/replace (rather than whole-file rewrites or line numbers) keeps model
output small, makes every change reviewable, and fails loudly: a ``search``
block must match exactly once, otherwise we return a precise error that is
fed back to the planner on the next attempt.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass


class PatchError(ValueError):
    pass


@dataclass
class Edit:
    path: str
    search: str
    replace: str


def apply_edits(original: str, edits: list[Edit]) -> str:
    text = original
    for i, e in enumerate(edits, 1):
        if e.search == "":
            # empty search == append (used for new files / adding to the end)
            text = text + ("" if text.endswith("\n") or not text else "\n") + e.replace
            continue
        count = text.count(e.search)
        if count == 1:
            text = text.replace(e.search, e.replace, 1)
            continue
        # tolerate trailing-whitespace drift before giving up
        norm = "\n".join(line.rstrip() for line in e.search.splitlines())
        norm_text = "\n".join(line.rstrip() for line in text.splitlines())
        if norm and norm_text.count(norm) == 1:
            text = norm_text.replace(norm, e.replace.rstrip("\n"), 1) + (
                "\n" if original.endswith("\n") else "")
            continue
        if count > 1:
            raise PatchError(f"edit {i} on {e.path}: search block matches {count} times; "
                             "include more surrounding lines to make it unique")
        raise PatchError(f"edit {i} on {e.path}: search block not found."
                         f"{_closest_hint(text, e.search)}")
    return text


def _closest_hint(text: str, search: str) -> str:
    first = search.strip().splitlines()[0] if search.strip() else ""
    best = difflib.get_close_matches(first.strip(), [ln.strip() for ln in text.splitlines()], n=1)
    return f" Closest line in file: {best[0]!r}" if best else ""


def unified_diff(path: str, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}"))
