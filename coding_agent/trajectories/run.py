"""Run & React: execute → parse output → act (report, or hand off to edit to fix)."""

from __future__ import annotations

import re
import shlex

from coding_agent.feedback.parsers import parse_pytest, parse_ruff_json
from coding_agent.memory.session import CommandRecord
from coding_agent.trajectories.base import FINALIZE, GraphBuilder, Trajectory, register

WANTS_FIX_RE = re.compile(r"\b(and|then|&)\s+(fix|repair|resolve)\b", re.I)
LINT_RE = re.compile(r"\b(lint|linter|linting|ruff|flake8|style)\b", re.I)
EXPLICIT_RE = re.compile(r"`([^`]+)`|^\s*run\s+((?:python|pytest|ruff|git|ls)\b.*)$", re.I)


def run_execute(state, rt):
    goal = state["goal"]
    flags = dict(state["flags"])
    flags["wants_fix"] = bool(WANTS_FIX_RE.search(goal))
    m = EXPLICIT_RE.search(goal)
    if m:
        cmd = (m.group(1) or m.group(2)).strip()
        kind = "test" if "pytest" in cmd else "lint" if cmd.startswith("ruff") else "other"
    elif LINT_RE.search(goal):
        cmd, kind = f"{rt.config.commands['lint']} .", "lint"
    else:
        cmd, kind = rt.config.commands["test"], "test"
    if flags.get("dry_run"):
        return {"flags": flags, "status": "dry_run",
                "response": f"**Dry run:** would execute `{cmd}` in the workspace and "
                            + ("hand failures to the edit flow." if flags["wants_fix"]
                               else "summarise the results.")}
    fp = None
    if kind == "test":
        r = rt.tools.call("tree_fingerprint")
        fp = r.data.get("fingerprint") if r.ok else None
    rt.status(f"Running `{shlex.split(cmd)[0]} …`")
    res = rt.tools.call("run_command", command=cmd)
    if not res.ok:
        return {"flags": flags, "status": "failed",
                "response": f"Couldn't run `{cmd}`: {res.error}"}
    d = res.data
    report: dict = {"kind": kind, "command": cmd, "exit_code": d["exit_code"],
                    "timed_out": d.get("timed_out")}
    failures: list[dict] = []
    if kind == "test":
        rep = parse_pytest(d["stdout"], d["stderr"], d["exit_code"])
        failures = [f.to_dict() for f in rep.failures]
        report["summary"] = rep.summary()
    elif kind == "lint":
        issues = parse_ruff_json(d["stdout"], str(rt.config.workspace)) if "json" in cmd else []
        failures = [{"nodeid": f"{i.path}:{i.line}", "message": f"{i.code} {i.message}",
                     "file": i.path, "line": i.line} for i in issues]
        report["summary"] = f"{len(issues)} lint issue(s)" if issues or "json" in cmd else (
            "clean" if d["exit_code"] == 0 else "issues found")
    else:
        report["summary"] = f"exit code {d['exit_code']}"
        report["output"] = (d["stdout"] + d["stderr"])[-2500:]
    report["failures"] = failures
    rt.session.record_command(CommandRecord(rt.session.turn_no, cmd, kind, d["exit_code"],
                                            report["summary"], fp, failures))
    return {"flags": flags, "report": report}


def run_analyze(state, rt):
    rep = state.get("report")
    if not rep:
        return {}
    fails = rep["failures"]
    if not fails and rep["exit_code"] in (0, None) or (rep["kind"] != "other" and not fails
                                                       and rep["exit_code"] == 0):
        return {"status": "success", "response": f"✅ `{rep['command']}` — {rep['summary']}."}
    if rep["kind"] == "other":
        return {"status": "info", "response":
                f"`{rep['command']}` → {rep['summary']}\n```\n{rep.get('output', '')}\n```"}
    lines = [f"❌ `{rep['command']}` — {rep['summary']}", ""]
    for f in fails[:8]:
        loc = f"{f.get('file')}:{f.get('line')}" if f.get("line") else f.get("file", "")
        lines.append(f"- **{f['nodeid']}** ({loc})\n  {f['message'][:240]}")
    if state["flags"].get("wants_fix"):
        rt.status("Handing failures to the edit flow")
        goal = "Fix the failing tests:\n" + "\n".join(
            f"- {f['nodeid']}: {f['message'][:200]}" for f in fails[:5])
        extra = {**(state.get("extra") or {}), "next": "edit_locate",
                 "decision_reason": "user asked to fix failures"}
        return {"goal": goal, "trajectory": "edit", "extra": extra,
                "response": "\n".join(lines)}
    lines += ["", "Say **fix the failing test** and I'll diagnose and repair it."]
    return {"status": "failed", "response": "\n".join(lines)}


def build(b: GraphBuilder) -> None:
    b.node("run_execute", run_execute)
    b.node("run_analyze", run_analyze)
    b.branch("run_execute", lambda s: FINALIZE if s.get("status") else "run_analyze",
             ["run_analyze", FINALIZE])
    b.branch("run_analyze",
             lambda s: "edit_locate" if (s.get("extra") or {}).get("next") == "edit_locate"
             and not s.get("status") else FINALIZE,
             ["edit_locate", FINALIZE])


register(Trajectory(
    name="run",
    description="Run tests, linters or another allow-listed command and react to the output "
                "(summarise failures; fix them if asked).",
    examples=["Run the tests", "Check linting", "Run the tests and fix any failures"],
    patterns=[(r"^(run|rerun|re-run|execute|check|lint)\b", 1.5),
              (r"\b(run|rerun|re-run)\b.*\b(tests?|lint|linter|ruff|pytest)\b", 1.5)],
    entry="run_execute", build=build, memory_scope="run"))
