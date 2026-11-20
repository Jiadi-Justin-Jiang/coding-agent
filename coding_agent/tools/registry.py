"""MCP client + tool registry.

* Connects to any number of MCP servers (stdio) declared in config.
* Discovers their tools at start-up and namespaces them (``fs.read_file``).
* Maps *capabilities* (what the agent needs: ``read_file``, ``run_command``)
  onto concrete tools, so swapping in a community server or adding ten new
  tools is a config change, not a code change.
* Validates arguments against each tool's JSON schema before calling.
* Caches read-only tools (MCP ``readOnlyHint``) and invalidates on any write.
* Normalises every outcome into ``ToolResult`` — nodes never see exceptions.
"""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import jsonschema
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from coding_agent.observability.tracer import Tracer

DEFAULT_CAPABILITIES = {
    "list_directory": "fs.list_directory",
    "read_file": "fs.read_file",
    "write_file": "fs.write_file",
    "delete_file": "fs.delete_file",
    "search_code": "fs.search_code",
    "stat_files": "fs.stat_files",
    "tree_fingerprint": "fs.tree_fingerprint",
    "run_command": "shell.run_command",
}


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    error_kind: str | None = None   # validation | tool | transport | unknown_tool
    cached: bool = False

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class ToolSpec:
    name: str              # namespaced, e.g. fs.read_file
    server: str
    remote_name: str
    description: str
    input_schema: dict
    read_only: bool


@dataclass
class ServerConfig:
    name: str
    command: str
    args: list[str]
    env: dict[str, str] | None = None


class ToolRegistry:
    def __init__(self, servers: list[ServerConfig], tracer: Tracer,
                 capabilities: dict[str, str] | None = None, call_timeout: float = 180) -> None:
        self.server_configs = servers
        self.tracer = tracer
        self.capabilities = {**DEFAULT_CAPABILITIES, **(capabilities or {})}
        self.call_timeout = call_timeout
        self.tools: dict[str, ToolSpec] = {}
        self._sessions: dict[str, ClientSession] = {}
        self._cache: dict[str, dict] = {}
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._ready = threading.Event()
        self._shutdown: asyncio.Event | None = None
        self._startup_error: BaseException | None = None
        self._runner: Any = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> ToolRegistry:
        self._thread.start()
        self._runner = asyncio.run_coroutine_threadsafe(self._run(), self._loop)
        self._ready.wait(timeout=60)
        if self._startup_error:
            raise RuntimeError(f"MCP start-up failed: {self._startup_error}")
        if not self._ready.is_set():
            raise RuntimeError("MCP servers did not start within 60s")
        return self

    async def _run(self) -> None:
        self._shutdown = asyncio.Event()
        try:
            async with AsyncExitStack() as stack:
                for cfg in self.server_configs:
                    params = StdioServerParameters(command=cfg.command, args=cfg.args, env=cfg.env)
                    read, write = await stack.enter_async_context(stdio_client(params))
                    session = await stack.enter_async_context(ClientSession(read, write))
                    await session.initialize()
                    self._sessions[cfg.name] = session
                    listed = await session.list_tools()
                    for t in listed.tools:
                        ro = bool(t.annotations and t.annotations.readOnlyHint)
                        spec = ToolSpec(f"{cfg.name}.{t.name}", cfg.name, t.name,
                                        t.description or "", t.inputSchema or {}, ro)
                        self.tools[spec.name] = spec
                self._ready.set()
                await self._shutdown.wait()
        except BaseException as exc:  # surface start-up failures to the caller
            self._startup_error = exc
            self._ready.set()

    def close(self) -> None:
        if self._shutdown is not None:
            self._loop.call_soon_threadsafe(self._shutdown.set)
            try:
                self._runner.result(timeout=10)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)

    # ------------------------------------------------------------------ calling
    def resolve(self, capability_or_name: str) -> str | None:
        name = self.capabilities.get(capability_or_name, capability_or_name)
        return name if name in self.tools else None

    def has(self, capability: str) -> bool:
        return self.resolve(capability) is not None

    def describe(self) -> list[dict]:
        return [{"name": t.name, "description": t.description.split("\n")[0],
                 "read_only": t.read_only} for t in self.tools.values()]

    def call(self, capability: str, **args: Any) -> ToolResult:
        name = self.resolve(capability)
        if name is None:
            return ToolResult(False, error=f"no tool provides capability '{capability}'",
                              error_kind="unknown_tool")
        spec = self.tools[name]
        summary = _summarise_args(args)
        with self.tracer.span("tool", name, args=summary, summary=summary) as span:
            try:
                jsonschema.validate(args, spec.input_schema)
            except jsonschema.ValidationError as exc:
                span.ok = False
                span.attrs["error"] = exc.message
                return ToolResult(False, error=f"invalid arguments: {exc.message}",
                                  error_kind="validation")

            key = f"{name}:{json.dumps(args, sort_keys=True)}"
            if spec.read_only and key in self._cache:
                span.attrs["cached"] = True
                return ToolResult(True, self._cache[key], cached=True)
            if not spec.read_only:
                self._cache.clear()  # any side effect invalidates cached reads

            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._sessions[spec.server].call_tool(spec.remote_name, args), self._loop)
                raw = fut.result(timeout=self.call_timeout)
            except Exception as exc:
                span.ok = False
                span.attrs["error"] = str(exc)
                return ToolResult(False, error=f"transport error: {exc}", error_kind="transport")

            text = "".join(getattr(c, "text", "") for c in raw.content)
            if raw.isError:
                span.ok = False
                span.attrs["error"] = text[:300]
                return ToolResult(False, error=_clean_error(text), error_kind="tool")
            data = raw.structuredContent or _parse_json(text)
            if isinstance(data, dict) and set(data) == {"result"}:
                data = data["result"] if isinstance(data["result"], dict) else data
            if spec.read_only:
                self._cache[key] = data
            span.attrs["result"] = _summarise_result(data)
            return ToolResult(True, data)


def _parse_json(text: str) -> dict:
    try:
        val = json.loads(text)
        return val if isinstance(val, dict) else {"result": val}
    except json.JSONDecodeError:
        return {"text": text}


def _clean_error(text: str) -> str:
    # FastMCP prefixes "Error executing tool x: ..."
    return text.split(": ", 1)[1] if text.startswith("Error executing tool") else text


def _summarise_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        if k == "content":
            parts.append(f"content=<{len(v)} chars>")
        else:
            s = json.dumps(v) if not isinstance(v, str) else v
            parts.append(f"{k}={s[:60]}")
    return " ".join(parts)


def _summarise_result(data: dict) -> str:
    if "exit_code" in data:
        return f"exit={data['exit_code']}"
    if "entries" in data:
        return f"{len(data['entries'])} entries"
    if "matches" in data:
        return f"{len(data['matches'])} matches"
    if "content" in data:
        return f"{data.get('total_lines', '?')} lines"
    return ",".join(list(data)[:4])
