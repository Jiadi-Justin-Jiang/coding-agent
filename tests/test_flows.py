"""End-to-end trajectories against a real copy of the target project.

Only the model text is canned (ReplayLLM); MCP servers, the graph, memory,
lint, tests and rollback all run for real.
"""

import json

from coding_agent.tools.registry import ToolResult
from tests.conftest import rule

VALIDATION_PLAN = json.dumps({"summary": "validate titles", "edits": [{
    "path": "routes/tasks.py",
    "search": '    title = payload.get("title", "")\n',
    "replace": '    title = payload.get("title", "")\n    if not str(title).strip():\n'
               '        return jsonify({"error": "title required"}), 400\n'}]})

PAGINATE_FIX = json.dumps({"summary": "fix off-by-one", "edits": [{
    "path": "utils/helpers.py", "search": "    start = page * per_page\n",
    "replace": "    start = (page - 1) * per_page\n"}]})

BREAKING_PLAN = json.dumps({"summary": "rename", "edits": [{
    "path": "models.py", "search": "    id: int\n    title: str\n",
    "replace": "    id: int\n    name: str\n"}]})

NOOP_FIX = json.dumps({"summary": "try again", "edits": [{
    "path": "models.py", "search": "    name: str\n    description",
    "replace": "    name: str  # renamed\n    description"}]})


def tool_names(agent):
    return [s.name for s in agent.tracer.turn_spans() if s.kind == "tool"]


def test_listing_costs_one_tool_call(make_agent):
    agent = make_agent([rule("explore.explain", "a list of files")])
    res = agent.run("What files are in this project?")
    assert res["trajectory"] == "explore" and res["tool_calls"] == 1
    assert "one-line summaries" in agent.rt.llm.calls[0][1]


def test_edit_reads_before_writing_and_flags_preexisting_failure(make_agent, workspace):
    agent = make_agent([rule("edit.plan", VALIDATION_PLAN)])
    res = agent.run("Add validation to reject empty task titles")
    names = tool_names(agent)
    assert res["status"] == "success"
    assert names.index("fs.read_file") < names.index("fs.write_file")       # read before edit
    assert "shell.run_command" in names[names.index("fs.write_file"):]       # verified after
    assert "pre-existing" in res["response"] and "test_list_tasks_pagination" in res["response"]
    assert 'title required' in (workspace / "routes/tasks.py").read_text()


def test_run_then_fix_uses_session_memory_of_failures(make_agent, workspace):
    agent = make_agent([rule("edit.plan", PAGINATE_FIX, match="test_list_tasks_pagination")])
    assert agent.run("Run the tests")["status"] == "failed"
    res = agent.run("Fix the failing test")
    assert res["status"] == "success", res["response"]
    assert "(page - 1)" in (workspace / "utils/helpers.py").read_text()


def test_run_and_fix_chains_into_edit(make_agent):
    agent = make_agent([rule("edit.plan", PAGINATE_FIX)])
    res = agent.run("Run the tests and fix any failures")
    assert res["trajectory"] == "edit" and res["status"] == "success"


def test_unfixable_edit_is_bounded_and_rolled_back(make_agent, workspace):
    before = (workspace / "models.py").read_text()
    agent = make_agent([rule("edit.plan", BREAKING_PLAN), rule("edit.fix", NOOP_FIX, once=False)])
    res = agent.run("Rename the Task field title to name in the model")
    assert res["status"] == "rolled_back"
    assert (workspace / "models.py").read_text() == before           # byte-identical restore
    fixes = [c for c in agent.rt.llm.calls if c[0] == "edit.fix"]
    assert 1 <= len(fixes) <= agent.rt.config.limits["max_fix_attempts"]


def test_bad_patch_triggers_bounded_replan(make_agent):
    bad = json.dumps({"summary": "x", "edits": [{"path": "routes/tasks.py",
                                                "search": "does not exist", "replace": "y"}]})
    agent = make_agent([rule("edit.plan", bad), rule("edit.plan", VALIDATION_PLAN)])
    res = agent.run("Add validation to reject empty task titles")
    assert res["status"] == "success"
    assert "could not be applied" in agent.rt.llm.calls[1][1]          # error fed back


def test_undo_restores_and_refuses_to_clobber_external_edits(make_agent, workspace):
    original = (workspace / "routes/tasks.py").read_text()
    agent = make_agent([rule("edit.plan", VALIDATION_PLAN), rule("edit.plan", VALIDATION_PLAN)])
    agent.run("Add validation to reject empty task titles")
    assert agent.run("undo that")["status"] == "success"
    assert (workspace / "routes/tasks.py").read_text() == original
    agent.run("Add validation to reject empty task titles")
    (workspace / "routes/tasks.py").write_text("# user edited this\n")
    res = agent.run("undo that")
    assert res["status"] == "failed" and "modified after my edit" in res["response"]
    assert (workspace / "routes/tasks.py").read_text() == "# user edited this\n"


def test_correction_reverts_replans_and_stores_lesson(make_agent, workspace):
    revised = VALIDATION_PLAN.replace("title required", "title must not be blank")
    agent = make_agent([rule("edit.plan", VALIDATION_PLAN),
                        rule("edit.plan", revised, match="REJECTED")])
    agent.run("Add validation to reject empty task titles")
    res = agent.run("That's wrong — the message should say the title must not be blank")
    text = (workspace / "routes/tasks.py").read_text()
    assert res["status"] == "success" and "must not be blank" in text and "title required" not in text
    assert agent.rt.persistent.lessons and agent.rt.journal.transactions[0].status == "rolled_back"


def test_preference_persists_and_is_enforced_next_session(make_agent, workspace):
    first = make_agent()
    assert first.run("Always add docstrings when you edit functions. Remember that.")["status"] == "success"
    first.close()
    plan = json.dumps({"summary": "add helper", "edits": [{"path": "utils/helpers.py", "search": "",
                       "replace": "\n\ndef double(x: int) -> int:\n    return 2 * x\n"}]})
    fix = json.dumps({"summary": "docstring", "edits": [{"path": "utils/helpers.py",
                      "search": "def double(x: int) -> int:\n",
                      "replace": 'def double(x: int) -> int:\n    """Return 2x."""\n'}]})
    second = make_agent([rule("edit.plan", plan), rule("edit.fix", fix, match="no docstring")])
    res = second.run("Add a double helper to utils/helpers.py")
    assert res["status"] == "success" and '"""Return 2x."""' in (workspace / "utils/helpers.py").read_text()
    assert "Always add docstrings" in second.rt.llm.calls[0][1]          # preference in prompt


def test_dry_run_writes_nothing(make_agent, workspace):
    before = (workspace / "routes/tasks.py").read_text()
    agent = make_agent([rule("edit.plan", VALIDATION_PLAN)])
    res = agent.run("dry run: add validation to reject empty task titles")
    assert res["status"] == "dry_run" and "```diff" in res["response"]
    assert "fs.write_file" not in tool_names(agent)
    assert (workspace / "routes/tasks.py").read_text() == before


def test_tool_failure_mid_edit_rolls_back_partial_writes(make_agent, workspace):
    two_files = json.dumps({"summary": "two files", "edits": [
        {"path": "routes/tasks.py", "search": '    title = payload.get("title", "")\n',
         "replace": '    title = payload.get("title", "") or "untitled"\n'},
        {"path": "models.py", "search": "class TaskStore:",
         "replace": "class TaskStore:  # touched"}]})
    agent = make_agent([rule("edit.plan", two_files)])
    real_call = agent.tools.call

    def flaky(capability, **args):        # fault injection: 2nd file write fails
        if capability == "write_file" and args.get("path") == "models.py":
            return ToolResult(False, error="disk full", error_kind="tool")
        return real_call(capability, **args)

    agent.rt.tools.call = flaky
    before = (workspace / "routes/tasks.py").read_text()
    res = agent.run("Make empty titles default to untitled")
    assert res["status"] == "failed" and "Rolled back" in res["response"]
    assert (workspace / "routes/tasks.py").read_text() == before
    agent.rt.tools.call = real_call
    assert agent.run("undo that")["trajectory"] == "correct"            # session still usable


def test_model_outage_is_reported_not_crashed(make_agent):
    agent = make_agent()     # no fixtures: every model call fails
    res = agent.run("Add validation to reject empty task titles")
    assert res["status"] == "failed" and "edit_plan" in res["response"]


def test_cache_avoids_rereading_unchanged_files(make_agent):
    agent = make_agent([rule("explore.explain", "x", once=False)])
    agent.run("Show me how tasks are created")
    agent.run("Show me how tasks are created")
    assert "fs.read_file" not in tool_names(agent)
