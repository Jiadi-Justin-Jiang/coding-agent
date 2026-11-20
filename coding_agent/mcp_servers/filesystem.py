"""Filesystem MCP server, sandboxed to a workspace root.

Run: python -m coding_agent.mcp_servers.filesystem --root ./target_project

Design notes
* Every write supports ``expected_sha256`` (optimistic concurrency): the agent
  never clobbers a file that changed since it was read.
* ``dry_run`` returns the unified diff without touching disk.
* ``list_directory(include_summary=True)`` returns each file's first
  docstring/comment line so "what files are in this project?" costs one call.
"""

from __future__ import annotations

import ast
import difflib
import fnmatch
import hashlib
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from coding_agent.mcp_servers._common import IGNORED_DIRS, parse_root, resolve_in_root

ROOT, _ARGS = parse_root()
mcp = FastMCP("filesystem", log_level="WARNING")
READ_ONLY = ToolAnnotations(readOnlyHint=True)
MAX_READ_BYTES = 200_000


def _sha(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix() or "."


def _iter_files(base: Path, recursive: bool = True):
    for p in sorted(base.rglob("*") if recursive else base.iterdir()):
        if any(part in IGNORED_DIRS for part in p.relative_to(ROOT).parts):
            continue
        yield p


def _summary(p: Path) -> str:
    """Cheap one-line description: module docstring, else first comment/heading."""
    try:
        text = p.read_text(errors="replace")[:4000]
    except OSError:
        return ""
    if p.suffix == ".py":
        try:
            doc = ast.get_docstring(ast.parse(text))
            if doc:
                return doc.strip().splitlines()[0]
        except SyntaxError:
            pass
        defs = re.findall(r"^(?:def|class) (\w+)", text, re.M)
        if defs:
            return "defines " + ", ".join(defs[:6])
    for line in text.splitlines():
        s = line.strip().lstrip("#").strip()
        if s:
            return s[:120]
    return ""


@mcp.tool(annotations=READ_ONLY)
def list_directory(path: str = ".", recursive: bool = False,
                   include_summary: bool = False, max_entries: int = 300) -> dict:
    """List files/directories under ``path`` (relative to the workspace root)."""
    base = resolve_in_root(ROOT, path)
    if not base.is_dir():
        raise ValueError(f"not a directory: {path}")
    entries = []
    for p in _iter_files(base, recursive):
        item = {"path": _rel(p), "type": "dir" if p.is_dir() else "file"}
        if p.is_file():
            item["size"] = p.stat().st_size
            if include_summary:
                item["summary"] = _summary(p)
        entries.append(item)
        if len(entries) >= max_entries:
            return {"entries": entries, "truncated": True}
    return {"entries": entries, "truncated": False}


@mcp.tool(annotations=READ_ONLY)
def read_file(path: str, start_line: int | None = None, end_line: int | None = None) -> dict:
    """Read a UTF-8 text file. Optional 1-indexed inclusive line range."""
    p = resolve_in_root(ROOT, path)
    if not p.is_file():
        raise FileNotFoundError(f"no such file: {path}")
    if p.stat().st_size > MAX_READ_BYTES:
        raise ValueError(f"file too large ({p.stat().st_size} bytes); read a line range")
    text = p.read_text()
    lines = text.splitlines(keepends=True)
    content = text
    if start_line or end_line:
        s = max(1, start_line or 1)
        e = min(len(lines), end_line or len(lines))
        content = "".join(lines[s - 1:e])
    return {"path": _rel(p), "content": content, "sha256": _sha(text),
            "total_lines": len(lines), "mtime": p.stat().st_mtime}


@mcp.tool()
def write_file(path: str, content: str, expected_sha256: str | None = None,
               dry_run: bool = False) -> dict:
    """Create or overwrite a file. Fails if ``expected_sha256`` doesn't match disk."""
    p = resolve_in_root(ROOT, path)
    existed = p.is_file()
    before = p.read_text() if existed else ""
    if expected_sha256 is not None and existed and _sha(before) != expected_sha256:
        raise ValueError(f"conflict: {path} changed on disk since it was read")
    diff = "".join(difflib.unified_diff(
        before.splitlines(keepends=True), content.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}"))
    if not dry_run:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return {"path": _rel(p), "created": not existed,
            "sha256_before": _sha(before), "sha256_after": _sha(content),
            "diff": diff, "written": not dry_run}


@mcp.tool()
def delete_file(path: str, expected_sha256: str | None = None, dry_run: bool = False) -> dict:
    """Delete a file (used by rollback of agent-created files)."""
    p = resolve_in_root(ROOT, path)
    if not p.is_file():
        raise FileNotFoundError(f"no such file: {path}")
    if expected_sha256 is not None and _sha(p.read_text()) != expected_sha256:
        raise ValueError(f"conflict: {path} changed on disk since it was written")
    if not dry_run:
        p.unlink()
    return {"path": _rel(p), "deleted": not dry_run}


@mcp.tool(annotations=READ_ONLY)
def search_code(pattern: str, glob: str = "*.py", regex: bool = False,
                ignore_case: bool = True, max_results: int = 60) -> dict:
    """Grep the workspace. Returns matching lines with file and line number."""
    flags = re.I if ignore_case else 0
    rx = re.compile(pattern if regex else re.escape(pattern), flags)
    matches = []
    for p in _iter_files(ROOT):
        if not p.is_file() or not fnmatch.fnmatch(p.name, glob):
            continue
        for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if rx.search(line):
                matches.append({"path": _rel(p), "line": i, "text": line.strip()[:200]})
                if len(matches) >= max_results:
                    return {"matches": matches, "truncated": True}
    return {"matches": matches, "truncated": False}


@mcp.tool(annotations=READ_ONLY)
def stat_files(paths: list[str]) -> dict:
    """Batch existence/hash check — lets the agent validate its read cache in one call."""
    out = []
    for rel in paths:
        p = resolve_in_root(ROOT, rel)
        if p.is_file():
            out.append({"path": rel, "exists": True, "sha256": _sha(p.read_text()),
                        "mtime": p.stat().st_mtime})
        else:
            out.append({"path": rel, "exists": False})
    return {"files": out}


@mcp.tool(annotations=READ_ONLY)
def tree_fingerprint(glob: str = "*.py") -> dict:
    """Hash of all matching files — identifies a workspace state (for test baselines)."""
    h = hashlib.sha256()
    n = 0
    for p in _iter_files(ROOT):
        if p.is_file() and fnmatch.fnmatch(p.name, glob):
            h.update(_rel(p).encode())
            h.update(p.read_bytes())
            n += 1
    return {"fingerprint": h.hexdigest()[:16], "files": n}


if __name__ == "__main__":
    mcp.run()
