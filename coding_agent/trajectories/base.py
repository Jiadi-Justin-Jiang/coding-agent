"""Trajectory plug-in system.

A trajectory is a self-contained sub-graph plus the metadata the router needs
(description, examples, regex patterns). Adding a new behaviour = one module
that calls ``register(Trajectory(...))``; the core graph, router and CLI pick
it up automatically.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from langgraph.graph import StateGraph

from coding_agent.runtime import Runtime
from coding_agent.state import AgentState

NodeFn = Callable[[AgentState, Runtime], dict]

RECOVER = "recover"
FINALIZE = "finalize"


@dataclass
class Trajectory:
    name: str
    description: str
    examples: list[str]
    patterns: list[tuple[str, float]]       # (regex, weight) for rule-based routing
    entry: str                              # entry node name
    build: Callable[[GraphBuilder], None]
    memory_scope: str = "all"               # which preferences apply
    meta: dict = field(default_factory=dict)


REGISTRY: dict[str, Trajectory] = {}


def register(t: Trajectory) -> Trajectory:
    REGISTRY[t.name] = t
    return t


class GraphBuilder:
    """Thin wrapper over StateGraph that adds tracing + uniform failure routing.

    * every node runs inside a trace span
    * an exception in any node becomes ``state.error`` (never crashes the REPL)
    * every edge first checks ``state.error`` and diverts to ``recover``
    * a global step budget stops runaway loops even if a router is buggy
    """

    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self.graph = StateGraph(AgentState)
        self.nodes: set[str] = set()

    def node(self, name: str, fn: NodeFn) -> None:
        rt = self.rt
        max_steps = rt.config.limits["max_steps"]

        def run(state: AgentState) -> dict:
            steps = state.get("steps", 0) + 1
            with rt.tracer.span("node", name) as span:
                if steps > max_steps:
                    span.ok = False
                    return {"steps": steps, "error": {
                        "node": name, "message": f"step budget exceeded ({max_steps})"}}
                try:
                    update = fn(state, rt) or {}
                except Exception as exc:  # noqa: BLE001 — convert to state, route to recover
                    span.ok = False
                    span.attrs["error"] = f"{type(exc).__name__}: {exc}"
                    update = {"error": {"node": name, "message": f"{type(exc).__name__}: {exc}"}}
                update["steps"] = steps
                extra = state.get("extra") or {}
                if "extra" not in update and extra.get("decision_reason"):
                    update["extra"] = {**extra, "decision_reason": ""}  # don't leak stale reasons
                return update

        self.graph.add_node(name, run)
        self.nodes.add(name)

    def edge(self, src: str, dst: str) -> None:
        self.graph.add_conditional_edges(
            src, lambda s: RECOVER if s.get("error") else dst, [dst, RECOVER])

    def branch(self, src: str, fn: Callable[[AgentState], str], targets: list[str]) -> None:
        def decide(s: AgentState) -> str:
            if s.get("error"):
                return RECOVER
            choice = fn(s)
            self.rt.tracer.event("decision", f"{src} → {choice}",
                                 reason=s.get("extra", {}).get("decision_reason", ""))
            return choice
        self.graph.add_conditional_edges(src, decide, list({*targets, RECOVER}))
