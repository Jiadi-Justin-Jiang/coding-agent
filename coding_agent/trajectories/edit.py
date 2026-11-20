"""Edit: locate → read → plan → apply → verify → (fix ↺ | rollback) → finalize.

Feedback sources wired into ``verify``:
* linter      — fixable issues are auto-fixed by ruff (no model call); the rest
                are fed to the fixer before the user ever sees the result
* preferences — enforceable policies (e.g. docstrings) checked via AST
* tests       — only *regressions* (or still-failing target tests) count;
                failures that existed before the edit are reported, not "fixed"

The fix loop is bounded by ``feedback/guard.should_retry``.
"""

from __future__ import annotations

import re

from coding_agent.edits.journal import sha256
from coding_agent.edits.patch import Edit, PatchError, apply_edits, unified_diff
from coding_agent.feedback.guard import should_retry
from coding_agent.feedback.parsers import parse_pytest, parse_ruff_json, signature
from coding_agent.feedback.policies import POLICIES
from coding_agent.memory.session import CommandRecord
from coding_agent.memory.text import keywords
from coding_agent.prompts import PLAN_SCHEMA, PLAN_SYSTEM, render_files, render_memory
from coding_agent.runtime import Runtime
from coding_agent.state import AgentState
from coding_agent.trajectories.base import FINALIZE, GraphBuilder, Trajectory, register
from coding_agent.trajectories.common import locate, project_index, read_files, test_file_for

FAILING_RE = re.compile(r"\b(fail(ing|ed|ures?)?|broken|red)\b.*\btests?\b|\btests?\b.*\bfail", re.I)


# --------------------------------------------------------------------------- locate / read
def edit_locate(state: AgentState, rt: Runtime) -> dict:
    if state.get("targets"):           # set by a correction / run→fix hand-off
        return {}
    goal = state["goal"]
    k = rt.config.limits["max_files_per_task"]
    hints: list[str] = []
    extra: list[str] = []
    fix_targets: list[str] = []
    last_test = rt.session.last_command("test")
    if FAILING_RE.search(goal) and last_test and last_test.failures:
        # Session memory tells us *which* tests failed and where.
        fix_targets = [f["nodeid"] for f in last_test.failures]
        for f in last_test.failures:
            if f.get("file"):
                hints.append(f["file"])
            extra += keywords(f["nodeid"].split("::")[-1].removeprefix("test_"))
        rt.tracer.event("memory", "using last test failures",
                        summary=", ".join(fix_targets))
    targets, reason = locate(rt, goal, k=k, hints=hints, extra_terms=extra,
                             drop_terms=("fail", "test", "fix") if fix_targets else ())
    rt.tracer.event("decision", "locate", reason=f"{targets} ({reason})")
    if not targets:
        return {"response": "I couldn't work out which file to change. "
                            "Mention the file or function name and I'll take it from there.",
                "status": "info", "targets": []}
    return {"targets": targets, "fix_targets": fix_targets}


def edit_read(state: AgentState, rt: Runtime) -> dict:
    files, missing = read_files(rt, state["targets"])
    if not files:
        return {"response": f"Could not read {', '.join(missing)}.", "status": "failed"}
    return {"files": files}


# --------------------------------------------------------------------------- plan / fix
def _plan(state: AgentState, rt: Runtime, mode: str) -> dict:
    goal = state["goal"]
    parts = [f"Goal: {goal}", render_memory(state.get("context") or {}),
             "Files (exact current contents):\n" + render_files(state["files"])]
    if state.get("patch_error"):
        parts.append(f"Your previous edit could not be applied: {state['patch_error']}\n"
                     "Copy search blocks exactly from the current file contents above.")
    if mode == "fix":
        v = state["verification"]
        txn = rt.journal.get(state.get("txn_id"))
        diff = "\n".join(unified_diff(c.path, c.before or "", c.after)
                         for c in txn.changes.values()) if txn else ""
        parts.append("Your change so far:\n" + diff)
        parts.append("Verification found these problems — fix them:\n- " +
                     "\n- ".join(v["problems"]))
        if v.get("snippets"):
            parts.append("Test output:\n" + "\n\n".join(v["snippets"])[:4000])
    purpose = "edit.fix" if mode == "fix" else "edit.plan"
    rt.status("Fixing problems found during verification" if mode == "fix" else "Planning edit")
    plan = rt.llm.complete_json(PLAN_SYSTEM, "\n\n".join(p for p in parts if p),
                                purpose, PLAN_SCHEMA, max_tokens=4000)
    rt.tracer.event("decision", purpose, reason=plan["summary"][:200],
                    summary=f"{len(plan['edits'])} edit(s)")
    extra = dict(state.get("extra") or {})
    if mode == "plan":
        extra["plan_summary"] = plan["summary"]
    else:
        extra["fix_summaries"] = [*extra.get("fix_summaries", []), plan["summary"]]
    if not plan["edits"]:
        return {"plan": plan, "patch_error": None, "response": plan["summary"], "status": "info",
                "extra": extra}
    return {"plan": plan, "patch_error": None, "extra": extra}


def edit_plan(state: AgentState, rt: Runtime) -> dict:
    return _plan(state, rt, "plan")


def edit_fix(state: AgentState, rt: Runtime) -> dict:
    attempts = dict(state.get("attempts") or {})
    attempts["fix"] = attempts.get("fix", 0) + 1
    return {**_plan(state, rt, "fix"), "attempts": attempts}


# --------------------------------------------------------------------------- apply
def edit_apply(state: AgentState, rt: Runtime) -> dict:
    files = {p: dict(f) for p, f in state["files"].items()}
    by_path: dict[str, list[Edit]] = {}
    for e in state["plan"]["edits"]:
        by_path.setdefault(e["path"].lstrip("./"), []).append(Edit(**e))
    new_contents: dict[str, tuple[str | None, str]] = {}
    try:
        for path, edits in by_path.items():
            if path not in files and any(e.search for e in edits):
                raise PatchError(f"{path} was not read; only edit files shown to you "
                                 "(or create a new file with an empty search)")
            before = files[path]["content"] if path in files else None
            new_contents[path] = (before, apply_edits(before or "", edits))
    except PatchError as exc:
        attempts = dict(state.get("attempts") or {})
        attempts["plan"] = attempts.get("plan", 0) + 1
        rt.tracer.event("feedback", "patch rejected", summary=str(exc)[:200])
        return {"patch_error": str(exc), "attempts": attempts}

    if state["flags"].get("dry_run"):
        diffs = "\n".join(unified_diff(p, b or "", a) for p, (b, a) in new_contents.items())
        checks = ["lint (ruff --fix)", "tests"] + rt.persistent.policy_checks()
        return {"status": "dry_run", "response":
                f"**Dry run — nothing was written.**\n\nPlan: {state['plan']['summary']}\n\n"
                f"```diff\n{diffs}```\n\nWould then run: {', '.join(checks)}; "
                f"auto-fix up to {rt.config.limits['max_fix_attempts']}× on failure, "
                "otherwise roll back."}

    txn = rt.journal.get(state.get("txn_id"))
    update: dict = {}
    if txn is None:
        fp = rt.tools.call("tree_fingerprint")
        update["baseline_fp"] = fp.data.get("fingerprint") if fp.ok else None
        txn = rt.journal.begin(rt.session.turn_no, state["goal"])
        update["txn_id"] = txn.id
    for path, (before, after) in new_contents.items():
        if before == after:
            continue
        res = rt.tools.call("write_file", path=path, content=after,
                            expected_sha256=files[path]["sha256"] if path in files else None)
        if not res.ok:
            raise RuntimeError(f"write to {path} failed: {res.error}")
        txn.record(path, before, after)
        files[path] = {"content": after, "sha256": res.data["sha256_after"]}
        rt.session.remember_file(path, after, res.data["sha256_after"])
    rt.status(f"Applied edit to {', '.join(new_contents)}")
    return {**update, "files": files, "patch_error": None}


def after_apply(state: AgentState) -> str:
    if state.get("status") == "dry_run":
        return FINALIZE
    if state.get("patch_error"):
        return "edit_patch_retry"
    return "edit_verify"


def edit_patch_retry(state: AgentState, rt: Runtime) -> dict:
    """Decide whether to re-plan after a rejected patch (bounded)."""
    limit = rt.config.limits["max_plan_retries"]
    if state["attempts"].get("plan", 0) > limit:
        return {"extra": {**(state.get("extra") or {}), "decision_reason": "patch retries exhausted"},
                "response": f"I couldn't produce an edit that applies cleanly after {limit} "
                            f"retries: {state['patch_error']}", "status": "failed"}
    mode = "edit_fix" if state.get("verification") else "edit_plan"
    update: dict = {"extra": {**(state.get("extra") or {}), "next": mode,
                              "decision_reason": "re-plan with patch error"}}
    # The model wants to edit a file we didn't give it: if it exists, read it and
    # re-plan with it in context (cheap context expansion instead of a failure).
    wanted = [e["path"].lstrip("./") for e in state["plan"]["edits"]
              if e["search"] and e["path"].lstrip("./") not in state["files"]]
    if wanted:
        more, _ = read_files(rt, wanted)
        if more:
            rt.tracer.event("decision", "expand context", reason=f"model asked for {list(more)}")
            update["files"] = {**state["files"], **more}
            update["targets"] = list(dict.fromkeys(state["targets"] + list(more)))
            update["patch_error"] = (f"{', '.join(more)} added to context — "
                                     "now plan the edit against its exact contents")
    return update


def after_patch_retry(state: AgentState) -> str:
    if state.get("status") == "failed":
        return "edit_rollback"
    return state["extra"]["next"]


# --------------------------------------------------------------------------- verify
def _lint(rt: Runtime, txn, files: dict) -> tuple[list[str], list[str]]:
    """Run ruff --fix on changed files. Returns (unfixed problems, auto-fixed files)."""
    py = [p for p in txn.files if p.endswith(".py") and txn.changes[p].after is not None]
    if not py:
        return [], []
    res = rt.tools.call("run_command", command=f"{rt.config.commands['lint_fix']} {' '.join(py)}")
    if not res.ok:  # linter unavailable → fall back to a syntax check
        rt.tracer.event("feedback", "linter unavailable", summary=res.error)
        res = rt.tools.call("run_command", command=f"{rt.config.commands['syntax']} {' '.join(py)}")
        if res.ok and res.data["exit_code"] != 0:
            return [f"syntax error: {res.data['stderr'].strip()[-300:]}"], []
        return [], []
    issues = parse_ruff_json(res.data["stdout"], str(rt.config.workspace))
    fixed: list[str] = []
    stat = rt.tools.call("stat_files", paths=py)
    for f in stat.data.get("files", []) if stat.ok else []:
        if f.get("exists") and f["sha256"] != sha256(txn.changes[f["path"]].after):
            # ruff rewrote the file: pull the new content into the transaction
            rd = rt.tools.call("read_file", path=f["path"])
            if rd.ok:
                txn.record(f["path"], txn.changes[f["path"]].before, rd.data["content"])
                files[f["path"]] = {"content": rd.data["content"], "sha256": rd.data["sha256"]}
                rt.session.remember_file(f["path"], rd.data["content"], rd.data["sha256"])
                fixed.append(f["path"])
    if fixed:
        rt.tracer.event("feedback", "lint auto-fix", summary=f"ruff fixed {', '.join(fixed)}")
    return [f"lint {i}" for i in issues], fixed


def _run_tests(rt: Runtime, fp: str | None):
    res = rt.tools.call("run_command", command=rt.config.commands["test"])
    if not res.ok:
        return None, res.error
    rep = parse_pytest(res.data["stdout"], res.data["stderr"], res.data["exit_code"])
    rt.session.record_command(CommandRecord(
        rt.session.turn_no, rt.config.commands["test"], "test", res.data["exit_code"],
        rep.summary(), fp, [f.to_dict() for f in rep.failures]))
    return rep, None


def _baseline_failures(rt: Runtime, state: AgentState, txn) -> set[str] | None:
    """Which tests failed BEFORE this edit? Memory first; otherwise measure once."""
    fp = state.get("baseline_fp")
    known = rt.session.test_result_for(fp) if fp else None
    if known is not None:
        rt.tracer.event("memory", "baseline from session", summary=known.summary)
        return {f["nodeid"] for f in known.failures}
    # Not in memory: temporarily swap the original files back, run tests, re-apply.
    rt.status("Measuring test baseline (pre-edit) to separate regressions from old failures")
    for c in txn.changes.values():
        if c.before is None:
            rt.tools.call("delete_file", path=c.path, expected_sha256=sha256(c.after))
        else:
            rt.tools.call("write_file", path=c.path, content=c.before,
                          expected_sha256=sha256(c.after))
    try:
        rep, err = _run_tests(rt, fp)
    finally:
        for c in txn.changes.values():
            rt.tools.call("write_file", path=c.path, content=c.after)
    return rep.failing_ids if rep else None


def edit_verify(state: AgentState, rt: Runtime) -> dict:
    txn = rt.journal.get(state["txn_id"])
    files = {p: dict(f) for p, f in state["files"].items()}
    rt.status("Verifying: lint → preferences → tests")
    lint_problems, autofixed = _lint(rt, txn, files)

    policy_problems: list[str] = []
    for check in rt.persistent.policy_checks():
        for c in txn.changes.values():
            policy_problems += POLICIES[check](c.path, c.before, c.after)

    report, snippets, regressions, unresolved, preexisting = None, [], [], [], []
    syntax_broken = any("E999" in p or "syntax" in p.lower() for p in lint_problems)
    if not syntax_broken:
        fp = rt.tools.call("tree_fingerprint")
        report, err = _run_tests(rt, fp.data.get("fingerprint") if fp.ok else None)
        if report is None:
            lint_problems.append(f"could not run tests: {err}")
        elif report.failures:
            base = _baseline_failures(rt, state, txn) or set()
            targets = set(state.get("fix_targets") or [])
            for f in report.failures:
                if f.nodeid in targets:
                    unresolved.append(f)
                elif f.nodeid not in base:
                    regressions.append(f)
                else:
                    preexisting.append(f)
            snippets = [f"{f.nodeid}\n{f.snippet}" for f in regressions + unresolved][:4]

    problems = (lint_problems + policy_problems +
                [f"regression {f.nodeid}: {f.message[:200]}" for f in regressions] +
                [f"still failing {f.nodeid}: {f.message[:200]}" for f in unresolved])
    verification = {
        "problems": problems, "snippets": snippets,
        "autofixed": sorted({*(state.get("verification") or {}).get("autofixed", []), *autofixed}),
        "tests": report.summary() if report else "not run",
        "preexisting": [f.nodeid for f in preexisting],
        # line numbers shift between attempts; normalise them out for loop detection
        "signature": signature([re.sub(r"\d+", "#", p) for p in problems]),
        "count": len(problems),
    }
    history = list(state.get("history") or [])
    extra = dict(state.get("extra") or {})
    if not problems:
        txn.status = "committed"
        extra["next"], extra["decision_reason"] = FINALIZE, "all checks passed"
        return {"verification": verification, "files": files, "history": history,
                "extra": extra, "status": "success",
                "response": _success_message(state, rt, txn, verification)}

    ok, why = should_retry(state["attempts"].get("fix", 0),
                           rt.config.limits["max_fix_attempts"], history, verification)
    rt.tracer.event("feedback", "verification failed",
                    summary=f"{len(problems)} problem(s); {why}")
    history.append({"signature": verification["signature"], "count": len(problems)})
    extra["next"] = "edit_fix" if ok else "edit_rollback"
    extra["decision_reason"] = why
    return {"verification": verification, "files": files, "history": history, "extra": extra}


def _success_message(state, rt, txn, v) -> str:
    diff = "\n".join(unified_diff(c.path, c.before or "", c.after) for c in txn.changes.values())
    extra = state.get("extra") or {}
    lines = [f"✅ {extra.get('plan_summary') or state['plan']['summary']}", "",
             f"```diff\n{diff}```", "",
             f"**Verified:** lint clean · tests: {v['tests']}"]
    if v["autofixed"]:
        lines.append(f"- ruff auto-fixed lint issues in {', '.join(v['autofixed'])}")
    for i, fix in enumerate(extra.get("fix_summaries", []), 1):
        lines.append(f"- self-correction {i}: {fix}")
    if v["preexisting"]:
        lines.append(f"- ⚠️ pre-existing failure(s), not caused by this edit: "
                     f"{', '.join(v['preexisting'])}")
    lines.append(f"- say **undo** to revert (transaction `{txn.id}`)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- rollback
def edit_rollback(state: AgentState, rt: Runtime) -> dict:
    txn = rt.journal.get(state.get("txn_id"))
    v = state.get("verification") or {}
    reason = (state.get("extra") or {}).get("decision_reason", "")
    problems = "\n".join(f"- {p}" for p in v.get("problems", [])[:6])
    if txn is None or not txn.changes:
        return {"status": "failed", "response": state.get("response") or "No changes were made."}
    if rt.config.raw.get("on_unresolved_failure") == "keep":
        txn.status = "committed"
        return {"status": "failed", "response":
                f"⚠️ Edit applied but verification still fails ({reason}):\n{problems}\n"
                "Kept the change as configured; say **undo** to revert."}
    res = rt.journal.rollback(txn, rt.tools)
    for p in res["restored"]:
        rt.session.invalidate(p)
    rt.session.notes.append(f"attempt to '{txn.goal[:80]}' was rolled back: {reason}")
    msg = [f"↩️ Stopped and rolled back — {reason}.", "", "Unresolved problems:", problems,
           "", f"Restored: {', '.join(res['restored']) or 'nothing'}."]
    if res["conflicts"]:
        msg.append(f"Not restored (edited outside the agent): {', '.join(res['conflicts'])}")
    msg.append("Tell me more about the intended behaviour and I'll try a different approach.")
    return {"status": "rolled_back", "response": "\n".join(msg)}


# --------------------------------------------------------------------------- wiring
def build(b: GraphBuilder) -> None:
    for name, fn in [("edit_locate", edit_locate), ("edit_read", edit_read),
                     ("edit_plan", edit_plan), ("edit_apply", edit_apply),
                     ("edit_patch_retry", edit_patch_retry), ("edit_verify", edit_verify),
                     ("edit_fix", edit_fix), ("edit_rollback", edit_rollback)]:
        b.node(name, fn)
    b.branch("edit_locate", lambda s: "edit_read" if s.get("targets") else FINALIZE,
             ["edit_read", FINALIZE])
    b.branch("edit_read", lambda s: "edit_plan" if s.get("files") else FINALIZE,
             ["edit_plan", FINALIZE])
    b.branch("edit_plan", lambda s: FINALIZE if s.get("status") == "info" else "edit_apply",
             ["edit_apply", FINALIZE])
    b.branch("edit_fix", lambda s: "edit_rollback" if s.get("status") == "info" else "edit_apply",
             ["edit_apply", "edit_rollback"])
    b.branch("edit_apply", after_apply, ["edit_verify", "edit_patch_retry", FINALIZE])
    b.branch("edit_patch_retry", after_patch_retry, ["edit_plan", "edit_fix", "edit_rollback"])
    b.branch("edit_verify", lambda s: s["extra"]["next"], [FINALIZE, "edit_fix", "edit_rollback"])
    b.edge("edit_rollback", FINALIZE)


register(Trajectory(
    name="edit",
    description="Change code: add features/validation, fix bugs or failing tests, refactor. "
                "Edits are verified with lint + tests and rolled back if they can't be fixed.",
    examples=["Add validation to reject empty task titles", "Fix the failing test",
              "Return 404 when a task doesn't exist"],
    patterns=[(r"(^|[.!?]\s+)(add|change|modify|update|fix|implement|refactor|rename|remove|"
               r"delete|create|make|replace|handle|validate|write|return|reject)\b", 1.2)],
    entry="edit_locate", build=build, memory_scope="edit"))

_ = project_index, test_file_for  # re-exported helpers used by other trajectories
