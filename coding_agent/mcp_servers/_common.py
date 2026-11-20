"""Shared helpers for the bundled MCP servers."""

from __future__ import annotations

import argparse
from pathlib import Path

IGNORED_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache",
    ".ruff_cache", ".mypy_cache", ".agent",
}


class SandboxError(ValueError):
    """Raised when a path escapes the workspace root."""


def resolve_in_root(root: Path, rel: str) -> Path:
    """Resolve ``rel`` inside ``root`` and refuse anything that escapes it."""
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise SandboxError(f"path '{rel}' is outside the workspace")
    return candidate


def parse_root() -> tuple[Path, argparse.Namespace]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--allow", default="")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"workspace root does not exist: {root}")
    return root, args
