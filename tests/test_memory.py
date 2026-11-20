import json

from coding_agent.memory.persistent import PersistentMemory
from coding_agent.memory.session import CommandRecord, SessionMemory


def test_file_cache_lru_eviction_keeps_summary():
    s = SessionMemory(max_cached_files=2)
    for i in range(3):
        s.remember_file(f"f{i}.py", f'"""File {i}."""\n', f"h{i}")
    assert s.cached("f0.py") is None and "f0.py" in s.files     # content evicted, record kept
    assert s.cached("f2.py").content and s.evictions == 1


def test_stale_cache_is_never_served():
    s = SessionMemory()
    s.remember_file("a.py", "x = 1\n", "h1")
    assert s.cached("a.py", "h1") is not None
    assert s.cached("a.py", "h2") is None          # hash changed on disk
    assert s.cached("a.py", "h1") is None          # and it stays dropped


def test_compaction_folds_old_turns_into_summary():
    s = SessionMemory(keep_recent_turns=2, summary_char_cap=10_000)
    for i in range(5):
        s.start_turn()
        s.end_turn(f"request {i}", "edit", "success: ok", [f"f{i}.py"])
    assert len(s.turns) == 2 and len(s.rolling_summary) == 3
    assert "(earlier) T1" in s.build_context("request 1 f1.py")


def test_context_is_budgeted_and_relevance_ranked():
    s = SessionMemory()
    s.start_turn()
    s.record_command(CommandRecord(1, "pytest", "test", 1, "1 failed",
                                   failures=[{"nodeid": "t::test_paginate", "message": "boom"}]))
    for i in range(30):
        s.start_turn()
        s.end_turn(f"unrelated chatter number {i} " * 5, "chat", "info: ok", [])
    ctx = s.build_context("fix paginate failure", budget_tokens=150)
    assert "test_paginate" in ctx
    assert len(ctx) <= 150 * 4 + 50


def test_preferences_dedupe_scope_and_policy(tmp_path):
    m = PersistentMemory(tmp_path / "m.json")
    p, created = m.add_preference("Always add docstrings when you edit functions", "edit")
    assert created and p["check"] == "require_docstrings"
    _, created = m.add_preference("always add docstrings when you edit functions.", "edit")
    assert not created and len(m.preferences) == 1
    assert m.preferences_for("explore") == [] and m.policy_checks() == ["require_docstrings"]
    again = PersistentMemory(tmp_path / "m.json")        # survives a new session
    assert again.preferences[0]["text"].lower().startswith("always add docstrings")
    assert again.remove_preference("docstring") and not again.preferences


def test_lessons_retrieved_by_relevance(tmp_path):
    m = PersistentMemory(tmp_path / "m.json")
    m.add_lesson("use 'task not found' as the 404 message", context="return 404 for missing task")
    m.add_lesson("prefer pathlib over os.path", context="file handling helpers")
    hits = m.relevant_lessons("return 404 when the task is missing")
    assert len(hits) == 1 and "not found" in hits[0]["text"]


def test_corrupt_store_is_quarantined(tmp_path):
    path = tmp_path / "m.json"
    path.write_text("{not json")
    m = PersistentMemory(path)
    assert m.preferences == [] and (tmp_path / "m.corrupt.json").exists()
    m.add_preference("x y z rule")
    assert json.loads(path.read_text())["preferences"]
