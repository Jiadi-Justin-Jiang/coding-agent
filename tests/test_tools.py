def test_sandbox_validation_allowlist_and_cache(make_agent):
    tools = make_agent().tools
    assert not tools.call("read_file", path="../../etc/passwd").ok
    bad = tools.call("read_file", path=123)
    assert bad.error_kind == "validation"
    assert "not allow-listed" in tools.call("run_command", command="rm -rf /").error
    assert "operators" in tools.call("run_command", command="python -V && ls").error
    assert tools.call("unknown_capability").error_kind == "unknown_tool"
    first = tools.call("search_code", pattern="def ")
    assert tools.call("search_code", pattern="def ").cached and not first.cached
    tools.call("write_file", path="new.py", content="x = 1\n")     # side effect clears cache
    assert not tools.call("search_code", pattern="def ").cached


def test_write_conflict_and_dry_run(make_agent, workspace):
    tools = make_agent().tools
    original = (workspace / "main.py").read_text()
    res = tools.call("write_file", path="main.py", content="changed\n", dry_run=True)
    assert res.ok and "+changed" in res.data["diff"] and (workspace / "main.py").read_text() == original
    res = tools.call("write_file", path="main.py", content="x\n", expected_sha256="0" * 64)
    assert not res.ok and "conflict" in res.error
