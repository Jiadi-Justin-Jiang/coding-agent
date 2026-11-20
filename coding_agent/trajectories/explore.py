"""Read/Explore: find file → read → explain."""

from __future__ import annotations

import re

from coding_agent.prompts import EXPLAIN_SYSTEM, render_files, render_memory
from coding_agent.trajectories.base import FINALIZE, GraphBuilder, Trajectory, register
from coding_agent.trajectories.common import locate, project_index, read_files

LISTING_RE = re.compile(r"\b(what|which) files\b|\blist (the |all )?files\b|"
                        r"\b(project|repo|directory) (structure|layout)\b|\boverview\b", re.I)


def explore_locate(state, rt):
    if LISTING_RE.search(state["goal"]):
        rt.status("Listing project files")
        return {"listing": project_index(rt), "targets": []}
    if state.get("targets"):          # e.g. a correction re-explaining the same files
        return {}
    targets, reason = locate(rt, state["goal"], k=2, include_tests=False)
    rt.tracer.event("decision", "locate", reason=f"{targets} ({reason})")
    return {"targets": targets}


def explore_read(state, rt):
    files, missing = read_files(rt, state.get("targets") or [])
    return {"files": files}


def explore_explain(state, rt):
    goal = state["goal"]
    memory = render_memory(state.get("context") or {})
    if state.get("listing") is not None:
        listing = "\n".join(f"{e['path']} ({e.get('size', 0)} B): {e.get('summary', '')}"
                            for e in state["listing"])
        prompt = (f"Question: {goal}\n\nProject files with one-line summaries:\n{listing}\n\n"
                  "List each file with a brief description of its role.")
    elif state.get("files"):
        prompt = (f"Question: {goal}\n\n{memory}\n\nRelevant files (line-numbered):\n"
                  f"{render_files(state['files'], numbered=True)}")
    else:
        return {"response": "I couldn't find files related to that. Try naming a file or symbol.",
                "status": "info"}
    rt.status("Explaining")
    answer = rt.llm.complete(EXPLAIN_SYSTEM, prompt, "explore.explain")
    return {"response": answer, "status": "success"}


def build(b: GraphBuilder) -> None:
    b.node("explore_locate", explore_locate)
    b.node("explore_read", explore_read)
    b.node("explore_explain", explore_explain)
    b.branch("explore_locate",
             lambda s: "explore_explain" if s.get("listing") is not None else "explore_read",
             ["explore_explain", "explore_read"])
    b.edge("explore_read", "explore_explain")
    b.edge("explore_explain", FINALIZE)


register(Trajectory(
    name="explore",
    description="Answer questions about the code: list files, find where something lives, "
                "explain how something works. Read-only.",
    examples=["What files are in this project?", "Show me how tasks are created",
              "Where is pagination implemented?"],
    patterns=[(r"^(what|how|where|why|which|who|show|explain|describe|list|tell|walk|find)\b", 1.0),
              (r"\?\s*$", 0.5)],
    entry="explore_locate", build=build, memory_scope="explore"))
