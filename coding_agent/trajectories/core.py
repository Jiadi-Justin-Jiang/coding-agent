"""Core nodes shared by every request: route → load_context → … → finalize."""

from __future__ import annotations

import re

from coding_agent.runtime import Runtime
from coding_agent.state import AgentState

DRY_RUN_RE = re.compile(r"^\s*(dry[- ]run[:,]?|what would happen if (you|i)?)\s*", re.I)


def route_node(state: AgentState, rt: Runtime) -> dict:
    from coding_agent.router import route
    text = state["user_input"]
    flags = dict(state.get("flags") or {})
    goal = text
    if DRY_RUN_RE.match(text):
        flags["dry_run"] = True
        goal = DRY_RUN_RE.sub("", text)
    flags["dry_run"] = flags.get("dry_run") or rt.dry_run
    decision = route(goal, rt)
    rt.tracer.event("decision", f"route → {decision['trajectory']}",
                    reason=f"{decision['source']} (conf {decision['confidence']}): "
                           f"{decision['reason']}")
    return {"route": decision, "trajectory": decision["trajectory"], "goal": goal,
            "flags": flags}


def load_context_node(state: AgentState, rt: Runtime) -> dict:
    """Retrieve only the memory relevant to this request (never dump everything)."""
    from coding_agent.trajectories.base import REGISTRY
    traj = REGISTRY[state["trajectory"]]
    goal = state["goal"]
    prefs = rt.persistent.preferences_for(traj.memory_scope)
    lessons = rt.persistent.relevant_lessons(goal)
    session_ctx = rt.session.build_context(goal, rt.config.memory["context_budget_tokens"])
    rt.tracer.event("memory", "retrieved context",
                    summary=f"{len(prefs)} prefs, {len(lessons)} lessons, "
                            f"{len(session_ctx)} chars session context")
    return {"context": {"preferences": prefs, "lessons": lessons, "session": session_ctx}}


def recover_node(state: AgentState, rt: Runtime) -> dict:
    """Any unexpected failure lands here: undo partial writes, explain, keep session alive."""
    err = state.get("error") or {}
    msg = [f"⚠️ Something failed in `{err.get('node', '?')}`: {err.get('message', 'unknown')}"]
    # The failing node may have opened a transaction without returning its id
    # (it raised mid-write), so consult the journal, not just graph state.
    for txn in rt.journal.open_transactions():
        if not txn.changes:
            txn.status = "rolled_back"
            continue
        res = rt.journal.rollback(txn, rt.tools)
        for p in res["restored"]:
            rt.session.invalidate(p)
        msg.append(f"Rolled back partial edits to: {', '.join(res['restored']) or 'none'}.")
        if res["conflicts"]:
            msg.append(f"Left untouched (changed outside the agent): {', '.join(res['conflicts'])}")
    msg.append("The session is intact — you can rephrase or retry.")
    return {"response": "\n".join(msg), "status": "failed", "error": None}


def finalize_node(state: AgentState, rt: Runtime) -> dict:
    """Promote the useful parts of working memory into session memory."""
    status = state.get("status") or "info"
    response = state.get("response") or "Done."
    txn = rt.journal.get(state.get("txn_id"))
    files = txn.files if txn else list(state.get("targets") or [])
    first = next((ln.strip("#* ") for ln in response.splitlines() if ln.strip()), "")
    rt.session.end_turn(state["user_input"], state.get("trajectory", "?"),
                        f"{status}: {first[:140]}", files)
    return {"status": status, "response": response}
