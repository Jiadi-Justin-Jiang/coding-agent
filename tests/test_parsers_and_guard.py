from coding_agent.feedback.guard import should_retry
from coding_agent.feedback.parsers import parse_pytest, parse_ruff_json, signature
from coding_agent.feedback.policies import require_docstrings

PYTEST_OUT = """\
.F.                                                                      [100%]
=================================== FAILURES ===================================
__________________________ test_list_tasks_pagination __________________________
tests/test_tasks.py:12: in test_list_tasks_pagination
    assert [t["title"] for t in items] == ["task 0", "task 1"]
E   AssertionError: assert ['task 2'] == ['task 0', 'task 1']
=========================== short test summary info ============================
FAILED tests/test_tasks.py::test_list_tasks_pagination - AssertionError: asse...
1 failed, 2 passed in 0.05s
"""


def test_parse_pytest_failure_details():
    rep = parse_pytest(PYTEST_OUT, "", 1)
    assert (rep.passed, rep.failed) == (2, 1)
    f = rep.failures[0]
    assert f.nodeid == "tests/test_tasks.py::test_list_tasks_pagination"
    assert f.file == "tests/test_tasks.py" and f.line == 12
    assert "['task 2']" in f.message  # full message, not the truncated summary line


def test_parse_pytest_collection_error():
    rep = parse_pytest("ImportError: cannot import name 'x'\n", "", 2)
    assert not rep.ok and rep.failures[0].nodeid == "<collection>"


def test_parse_ruff_json():
    out = '[{"code":"F401","message":"`os` imported but unused","filename":"/ws/a.py",' \
          '"location":{"row":1,"column":1},"fix":{"applicability":"safe"}}]'
    issues = parse_ruff_json(out, "/ws")
    assert issues[0].path == "a.py" and issues[0].fixable and issues[0].code == "F401"


def test_guard_budget_progress_and_regression():
    cur = {"signature": signature(["a"]), "count": 1}
    assert should_retry(0, 3, [], cur)[0]
    assert not should_retry(3, 3, [], cur)[0]
    ok, why = should_retry(1, 3, [{"signature": cur["signature"], "count": 1}], cur)
    assert not ok and "no progress" in why
    worse = {"signature": signature(["a", "b", "c"]), "count": 3}
    ok, why = should_retry(1, 3, [{"signature": "zzz", "count": 1}], worse)
    assert not ok and "worse" in why


def test_docstring_policy_only_checks_changed_functions():
    before = "def old():\n    return 1\n"
    after = before + "\n\ndef new():\n    return 2\n"
    problems = require_docstrings("m.py", before, after)
    assert len(problems) == 1 and "'new'" in problems[0]
    assert require_docstrings("m.py", before, before) == []
