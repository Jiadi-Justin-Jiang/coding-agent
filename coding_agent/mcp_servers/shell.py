"""Shell MCP server: runs allow-listed commands inside the workspace.

Run: python -m coding_agent.mcp_servers.shell --root ./target_project --allow python,pytest,ruff,git

Guardrails (not a sandbox — run the agent in a container for real isolation):
* no shell interpretation (argv only, no pipes/redirects/&&)
* executable allow-list, cwd pinned to the workspace, hard timeout
* output truncated head+tail so a noisy command can't blow the context window
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time

from mcp.server.fastmcp import FastMCP

from coding_agent.mcp_servers._common import parse_root

ROOT, ARGS = parse_root()
ALLOW = {a.strip() for a in ARGS.allow.split(",") if a.strip()}
TIMEOUT = ARGS.timeout
MAX_OUTPUT = 12_000
mcp = FastMCP("shell", log_level="WARNING")


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return f"{text[:half]}\n... [{len(text) - MAX_OUTPUT} chars truncated] ...\n{text[-half:]}"


@mcp.tool()
def run_command(command: str, timeout_s: int | None = None) -> dict:
    """Run an allow-listed command in the workspace root and return its output."""
    argv = shlex.split(command)
    if not argv:
        raise ValueError("empty command")
    if any(tok in {"|", "&&", "||", ";", ">", "<"} for tok in argv):
        raise ValueError("shell operators are not supported; run one command at a time")
    exe = argv[0]
    if exe not in ALLOW:
        raise PermissionError(f"'{exe}' is not allow-listed (allowed: {sorted(ALLOW)})")
    if exe in {"python", "python3"}:
        argv[0] = sys.executable  # same interpreter/env as the server
    elif shutil.which(exe) is None:
        raise FileNotFoundError(f"executable not found: {exe}")
    env = {k: v for k, v in os.environ.items() if not k.endswith(("_KEY", "_TOKEN", "_SECRET"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                              timeout=min(timeout_s or TIMEOUT, TIMEOUT), env=env)
    except subprocess.TimeoutExpired as exc:
        return {"command": command, "exit_code": None, "timed_out": True,
                "stdout": _truncate(exc.stdout or ""), "stderr": _truncate(exc.stderr or ""),
                "duration_ms": int((time.monotonic() - started) * 1000)}
    return {"command": command, "exit_code": proc.returncode, "timed_out": False,
            "stdout": _truncate(proc.stdout), "stderr": _truncate(proc.stderr),
            "duration_ms": int((time.monotonic() - started) * 1000)}


@mcp.tool()
def allowed_commands() -> dict:
    """Which executables this server will run."""
    return {"allowed": sorted(ALLOW), "cwd": str(ROOT), "timeout_s": TIMEOUT}


if __name__ == "__main__":
    mcp.run()
