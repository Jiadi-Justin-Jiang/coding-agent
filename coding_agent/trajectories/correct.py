"""Correction: "undo that" (revert) or "that's wrong, …" (revise with feedback).

Revise = roll back the previous transaction, then re-run the edit flow on the
same files with the original goal + the user's feedback + the rejected diff.
The feedback is also stored as a *lesson* in persistent memory, so similar
future tasks retrieve it.
"""

from __future__ import annotations

import re

from coding_agent.edits.patch import unified_diff
from coding_agent.trajectories.base import FINALIZE, GraphBuilder, Trajectory, register

UNDO_RE = re.compile(r"\b(undo|revert|roll ?back|go back|put it back)\b", re.I)


def correct_decide(state, rt):
    text = state["goal"]
    extra = dict(state.get("extra") or {})
    last_txn = rt.journal.last_committed()
    last_turn = rt.session.turns[-1] if rt.session.turns else None
    if UNDO_RE.search(text):
        extra["next"] = "correct_undo" if last_txn else FINALIZE
        extra["decision_reason"] = "explicit undo" if last_txn else "nothing to undo"
        if not last_txn:
            return {"extra": extra, "status": "info",
                    "response": "There's no committed edit to undo in this session."}
        return {"extra": extra}
    if last_turn and last_turn.trajectory == "explore":
        prev_q = last_turn.user
        extra["next"], extra["decision_reason"] = "explore_read", "feedback on an explanation"
        return {"extra": extra, "targets": last_turn.files or [],
                "goal": f"{prev_q}\n\nThe user said the previous explanation was wrong: {text}"}
    if last_txn:
        extra["next"], extra["decision_reason"] = "correct_revise", "feedback on last edit"
        return {"extra": extra}
    extra["next"] = FINALIZE
    return {"extra": extra, "status": "info",
            "response": "I'm not sure what to correct — there's no recent edit or explanation. "
                        "Could you say what should change?"}


def correct_undo(state, rt):
    txn = rt.journal.last_committed()
    res = rt.journal.rollback(txn, rt.tools)
    for p in res["restored"]:
        rt.session.invalidate(p)
    rt.session.notes.append(f"user undid the edit '{txn.goal[:80]}'")
    msg = [f"↩️ Reverted transaction `{txn.id}` (`{txn.goal.splitlines()[0][:80]}`)."
           f" Restored: {', '.join(res['restored']) or 'nothing'}."]
    if res["conflicts"]:
        msg.append(f"⚠️ Not restored because they were modified after my edit: "
                   f"{', '.join(res['conflicts'])}. Resolve manually or tell me what to keep.")
    if res["errors"]:
        msg.append("Errors: " + "; ".join(res["errors"]))
    status = "success" if not (res["conflicts"] or res["errors"]) else "failed"
    return {"status": status, "response": "\n".join(msg), "targets": res["restored"]}


def correct_revise(state, rt):
    txn = rt.journal.last_committed()
    feedback = state["goal"]
    diff = "\n".join(unified_diff(c.path, c.before or "", c.after) for c in txn.changes.values())
    res = rt.journal.rollback(txn, rt.tools)
    if res["conflicts"] or res["errors"]:
        return {"status": "failed", "response":
                "I couldn't cleanly revert my previous edit (files changed since). "
                f"Conflicts: {res['conflicts']} {res['errors']}"}
    for p in res["restored"]:
        rt.session.invalidate(p)
    lesson = rt.persistent.add_lesson(feedback, context=txn.goal)
    rt.session.notes.append(f"rejected '{txn.goal[:60]}': {feedback[:120]}")
    rt.tracer.event("memory", "stored lesson", summary=f"{lesson['text'][:80]} (×{lesson['count']})")
    rt.status("Reverted previous attempt; re-planning with your feedback")
    goal = (f"{txn.goal}\n\nThe user REJECTED the previous attempt with this feedback: "
            f"{feedback}\nRejected diff (already reverted — do not repeat it):\n{diff}")
    return {"goal": goal, "targets": txn.files, "trajectory": "edit",
            "context": {**(state.get("context") or {}),
                        "lessons": rt.persistent.relevant_lessons(goal)}}


def build(b: GraphBuilder) -> None:
    b.node("correct_decide", correct_decide)
    b.node("correct_undo", correct_undo)
    b.node("correct_revise", correct_revise)
    b.branch("correct_decide", lambda s: s["extra"]["next"],
             ["correct_undo", "correct_revise", "explore_read", FINALIZE])
    b.edge("correct_undo", FINALIZE)
    b.branch("correct_revise", lambda s: FINALIZE if s.get("status") else "edit_read",
             ["edit_read", FINALIZE])


register(Trajectory(
    name="correct",
    description="The user rejects or wants to revert the previous action: undo/revert an edit, "
                "or say it was wrong and how to revise it.",
    examples=["Undo that", "That's wrong — use 422 instead", "No, keep the old behaviour"],
    patterns=[(r"^(undo|revert|roll ?back)\b", 3.0), (r"\bundo\b", 2.0),
              (r"\b(that'?s|this is|it'?s|that was) (wrong|incorrect|not right|broken)\b", 2.5),
              (r"^(no|nope)[,.!]", 2.0), (r"\binstead\b", 0.6)],
    entry="correct_decide", build=build, memory_scope="edit"))
