"""Configuration (agent.yaml + env overrides)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULTS = {
    "workspace": "./target_project",
    "llm": {"provider": "anthropic", "model": "claude-sonnet-4-5"},
    "mcp_servers": {
        "fs": {"command": "{python}",
               "args": ["-m", "coding_agent.mcp_servers.filesystem", "--root", "{workspace}"]},
        "shell": {"command": "{python}",
                  "args": ["-m", "coding_agent.mcp_servers.shell", "--root", "{workspace}",
                           "--allow", "python,python3,pytest,ruff,git,ls"]},
    },
    "capabilities": {},
    "commands": {
        "test": "python -m pytest -q --tb=short -rfE -p no:cacheprovider",
        "lint": "ruff check --output-format json --no-cache",
        "lint_fix": "ruff check --fix --output-format json --no-cache",
        "syntax": "python -m py_compile",
    },
    "limits": {"max_fix_attempts": 3, "max_plan_retries": 2, "max_steps": 40,
               "max_files_per_task": 3},
    "memory": {"persistent_path": "~/.coding_agent/memory.json", "context_budget_tokens": 1200,
               "keep_recent_turns": 6, "max_cached_files": 8},
    "on_unresolved_failure": "rollback",   # rollback | keep
    "dry_run": False,
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


@dataclass
class Config:
    raw: dict
    workspace: Path
    base_dir: Path
    data: dict = field(default_factory=dict)

    @property
    def limits(self) -> dict:
        return self.raw["limits"]

    @property
    def memory(self) -> dict:
        return self.raw["memory"]

    @property
    def commands(self) -> dict:
        return self.raw["commands"]

    def server_configs(self):
        from coding_agent.tools.registry import ServerConfig
        repo_root = str(Path(__file__).resolve().parents[1])
        env = dict(os.environ)
        env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")
        out = []
        for name, spec in self.raw["mcp_servers"].items():
            sub = {"python": sys.executable, "workspace": str(self.workspace)}
            out.append(ServerConfig(
                name=name, command=spec["command"].format(**sub),
                args=[a.format(**sub) for a in spec.get("args", [])],
                env={**env, **spec.get("env", {})}))
        return out


def load_config(path: str | None = None, **overrides) -> Config:
    raw = dict(DEFAULTS)
    base_dir = Path.cwd()
    if path:
        p = Path(path)
        raw = _merge(raw, yaml.safe_load(p.read_text()) or {})
        base_dir = p.resolve().parent
    raw = _merge(raw, {k: v for k, v in overrides.items() if v is not None})
    if os.environ.get("AGENT_MEMORY_PATH"):
        raw["memory"]["persistent_path"] = os.environ["AGENT_MEMORY_PATH"]
    ws = Path(raw["workspace"]).expanduser()
    if not ws.is_absolute():
        ws = (base_dir / ws).resolve()
    return Config(raw=raw, workspace=ws, base_dir=base_dir)
