import pytest

from coding_agent.edits.patch import Edit, PatchError, apply_edits

SRC = "def a():\n    return 1\n\n\ndef b():\n    return 1\n"


def test_exact_unique_replace():
    out = apply_edits(SRC, [Edit("f.py", "def a():\n    return 1", "def a():\n    return 2")])
    assert "return 2" in out and out.count("return 1") == 1


def test_ambiguous_search_is_rejected_with_guidance():
    with pytest.raises(PatchError, match="matches 2 times"):
        apply_edits(SRC, [Edit("f.py", "    return 1", "    return 3")])


def test_missing_search_gives_closest_line_hint():
    with pytest.raises(PatchError, match="Closest line"):
        apply_edits(SRC, [Edit("f.py", "def a(x):", "def a(y):")])


def test_trailing_whitespace_drift_is_tolerated():
    out = apply_edits(SRC, [Edit("f.py", "def b():   \n    return 1", "def b():\n    return 9")])
    assert "return 9" in out


def test_empty_search_appends():
    out = apply_edits("x = 1\n", [Edit("f.py", "", "y = 2\n")])
    assert out == "x = 1\ny = 2\n"
