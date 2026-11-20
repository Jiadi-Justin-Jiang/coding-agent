"""Assemble the LangGraph state machine from the trajectory registry."""

from __future__ import annotations

from langgraph.graph import END, START

from coding_agent.runtime import Runtime
from coding_agent.trajectories import REGISTRY
from coding_agent.trajectories.base import FINALIZE, RECOVER, GraphBuilder
from coding_agent.trajectories.core import (
    finalize_node,
    load_context_node,
    recover_node,
    route_node,
)


def build_graph(rt: Runtime):
    b = GraphBuilder(rt)
    b.node("route", route_node)
    b.node("load_context", load_context_node)
    b.node(RECOVER, recover_node)
    b.node(FINALIZE, finalize_node)

    b.graph.add_edge(START, "route")
    b.edge("route", "load_context")
    b.branch("load_context", lambda s: REGISTRY[s["trajectory"]].entry,
             [t.entry for t in REGISTRY.values()])
    for traj in REGISTRY.values():
        traj.build(b)
    b.graph.add_edge(RECOVER, FINALIZE)
    b.graph.add_edge(FINALIZE, END)
    return b.graph.compile()
