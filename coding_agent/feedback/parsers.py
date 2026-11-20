"""Turn raw tool output into structured feedback signals."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed)")
_SHORT_RE = re.compile(r"^(FAILED|ERROR) (\S+)(?: - (.*))?$", re.M)
_SECTION_RE = re.compile(r"^_{3,} (.+?) _{3,}$", re.M)
_LOC_RE = re.compile(r"^([\w./-]+\.py):(\d+): ", re.M)


@dataclass
class Failure:
    nodeid: str
    kind: str              # failed | error
    message: str
    file: str | None = None
    line: int | None = None
    snippet: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class TestReport:
    passed: int = 0
    failed: int = 0
    errors: int = 0
    failures: list[Failure] = field(default_factory=list)
    ran: bool = True
    raw_tail: str = ""

    @property
    def ok(self) -> bool:
        return self.ran and self.failed == 0 and self.errors == 0

    @property
    def failing_ids(self) -> set[str]:
        return {f.nodeid for f in self.failures}

    def summary(self) -> str:
        if not self.ran:
            return "tests did not run"
        return f"{self.passed} passed, {self.failed} failed" + (
            f", {self.errors} errors" if self.errors else "")


def parse_pytest(stdout: str, stderr: str = "", exit_code: int | None = 0) -> TestReport:
    out = stdout + ("\n" + stderr if stderr else "")
    rep = TestReport(raw_tail=out[-1500:])
    last = [ln for ln in out.strip().splitlines() if _SUMMARY_RE.search(ln)]
    if last:
        for n, kind in _SUMMARY_RE.findall(last[-1]):
            n = int(n)
            if kind == "passed":
                rep.passed = n
            elif kind == "failed":
                rep.failed = n
            elif kind in ("error", "errors"):
                rep.errors = n
    elif exit_code not in (0, None) or "no tests ran" in out:
        rep.ran = exit_code == 5  # 5 == no tests collected
        if exit_code not in (0, 5):
            rep.ran, rep.errors = True, 1
            rep.failures.append(Failure("<collection>", "error", _last_error_line(out)))
            return rep

    # Full assertion text lives in the per-test sections; index them by test name.
    sections: dict[str, str] = {}
    marks = list(_SECTION_RE.finditer(out))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(out)
        body = out[m.end():end].split("short test summary info")[0]
        sections[m.group(1).strip()] = body

    for kind, nodeid, msg in _SHORT_RE.findall(out):
        name = nodeid.split("::")[-1]
        body = sections.get(name, "") or sections.get(f"ERROR at setup of {name}", "")
        e_lines = [ln[1:].strip() for ln in body.splitlines() if ln.startswith("E ")]
        locs = _LOC_RE.findall(body)
        file, line = (locs[-1][0], int(locs[-1][1])) if locs else (nodeid.split("::")[0], None)
        rep.failures.append(Failure(
            nodeid=nodeid, kind="failed" if kind == "FAILED" else "error",
            message=" ".join(e_lines)[:400] or (msg or "").strip(),
            file=file, line=line, snippet=body.strip()[-1200:]))
    return rep


def _last_error_line(out: str) -> str:
    lines = [ln for ln in out.splitlines() if ln.strip()]
    errs = [ln for ln in lines if "Error" in ln or ln.startswith("E ")]
    return (errs[-1] if errs else (lines[-1] if lines else "unknown error")).strip()[:300]


@dataclass
class LintIssue:
    code: str
    message: str
    path: str
    line: int
    fixable: bool

    def __str__(self) -> str:
        return f"{self.path}:{self.line} {self.code} {self.message}"


def parse_ruff_json(stdout: str, root_prefix: str = "") -> list[LintIssue]:
    try:
        items = json.loads(stdout or "[]")
    except json.JSONDecodeError:
        return [LintIssue("PARSE", stdout.strip()[:200], "?", 0, False)] if stdout.strip() else []
    issues = []
    for it in items:
        path = it.get("filename", "?")
        if root_prefix and path.startswith(root_prefix):
            path = path[len(root_prefix):].lstrip("/")
        issues.append(LintIssue(it.get("code") or "E999", it.get("message", ""), path,
                                (it.get("location") or {}).get("row", 0), bool(it.get("fix"))))
    return issues


def signature(*groups: list[str]) -> str:
    """Stable fingerprint of a failure set — used to detect 'no progress' loops."""
    flat = sorted(item for g in groups for item in g)
    return hashlib.sha1("|".join(flat).encode()).hexdigest()[:10]
