"""Enforceable preferences.

A preference like "always add docstrings when you edit functions" is not just
pasted into the prompt — it maps to a check that runs during verification. A
violation is treated like a lint error: fed back to the fixer automatically.
Add a new policy by writing a function and registering it in ``POLICIES``.
"""

from __future__ import annotations

import ast
from collections.abc import Callable


def _functions(src: str) -> dict[str, ast.AST]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {}
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _changed_functions(before: str | None, after: str) -> list[ast.AST]:
    old = _functions(before or "")
    new = _functions(after)
    out = []
    for name, node in new.items():
        prev = old.get(name)
        if prev is None or ast.dump(prev) != ast.dump(node):
            out.append(node)
    return out


def require_docstrings(path: str, before: str | None, after: str) -> list[str]:
    if not path.endswith(".py"):
        return []
    return [f"{path}:{fn.lineno} function '{fn.name}' was edited but has no docstring "
            f"(user preference: always add docstrings)"
            for fn in _changed_functions(before, after) if not ast.get_docstring(fn)]


def require_type_hints(path: str, before: str | None, after: str) -> list[str]:
    if not path.endswith(".py"):
        return []
    out = []
    for fn in _changed_functions(before, after):
        args = [a for a in fn.args.args if a.arg not in ("self", "cls")]
        if fn.returns is None or any(a.annotation is None for a in args):
            out.append(f"{path}:{fn.lineno} function '{fn.name}' lacks type hints "
                       "(user preference)")
    return out


POLICIES: dict[str, Callable[[str, str | None, str], list[str]]] = {
    "require_docstrings": require_docstrings,
    "require_type_hints": require_type_hints,
}
