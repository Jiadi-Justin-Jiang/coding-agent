"""Remember / forget user preferences (persistent memory). No model call needed."""

from __future__ import annotations

import re

from coding_agent.trajectories.base import FINALIZE, GraphBuilder, Trajectory, register

FORGET_RE = re.compile(r"^\s*(forget|stop|don'?t remember|remove (the )?preference)\b[: ]*", re.I)
STRIP_RE = re.compile(r"(\bplease\b|\bremember (that|this)\b[.:]?|\bremember\b[.!]?\s*$|"
                      r"^\s*(note|fyi)[:,]\s*)", re.I)
EDIT_SCOPE_RE = re.compile(r"\b(edit|change|write|modify|function|docstring|code|test|type hint|"
                           r"import|refactor|comment)s?\b", re.I)
EXPLORE_SCOPE_RE = re.compile(r"\b(explain|explanation|answer|summar)", re.I)


def remember_store(state, rt):
    text = state["goal"].strip()
    if FORGET_RE.match(text):
        needle = FORGET_RE.sub("", text)
        removed = rt.persistent.remove_preference(needle)
        if not removed:
            return {"status": "info", "response": f"No stored preference matches {needle!r}."}
        return {"status": "success", "response": "🗑️ Forgot: " +
                "; ".join(p["text"] for p in removed)}
    pref_text = re.sub(r"\s+", " ", STRIP_RE.sub("", text)).strip(" .")
    if not pref_text:
        return {"status": "info", "response": "What should I remember?"}
    scope = ("edit" if EDIT_SCOPE_RE.search(pref_text)
             else "explore" if EXPLORE_SCOPE_RE.search(pref_text) else "all")
    entry, created = rt.persistent.add_preference(pref_text, scope)
    enforced = (f" It's enforced automatically: every edit is checked by `{entry['check']}` "
                "and violations are fixed before you see the result.") if entry.get("check") else ""
    verb = "Saved" if created else "Updated"
    return {"status": "success", "response":
            f"🧠 {verb} preference `{entry['id']}` (scope: {scope}): “{entry['text']}”.{enforced}\n"
            f"Stored in `{_pretty(rt.persistent.path)}` — it applies to future sessions too."}


def _pretty(path) -> str:
    from pathlib import Path
    p = Path(path)
    for base, prefix in ((Path.cwd(), ""), (Path.home(), "~/")):
        try:
            return prefix + str(p.relative_to(base))
        except ValueError:
            continue
    return str(p)


def build(b: GraphBuilder) -> None:
    b.node("remember_store", remember_store)
    b.edge("remember_store", FINALIZE)


register(Trajectory(
    name="remember",
    description="Store or forget a lasting user preference about how the agent should work.",
    examples=["Always add docstrings when you edit functions. Remember that.",
              "From now on use type hints", "Forget the docstring preference"],
    patterns=[(r"\bremember (that|this)\b", 3.0), (r"\bremember\.?\s*$", 2.5),
              (r"^(always|never|from now on)\b", 2.0),
              (r"^(forget|stop)\b.*\b(preference|remember)", 3.0)],
    entry="remember_store", build=build))
