"""Hybrid intent router.

1. Rules: each trajectory contributes weighted regexes. Clear winners are
   routed with zero model calls (fast, deterministic, testable).
2. Model fallback: when no rule fires or the top two are close, the LLM picks
   from the registry's descriptions + examples, with the last turns as context
   (so "fix it" after a failing test run resolves correctly).
3. Validation: the model's answer must be a registered trajectory; otherwise
   we fall back to ``chat`` rather than guessing.
"""

from __future__ import annotations

import re

from coding_agent.prompts import ROUTER_SCHEMA, ROUTER_SYSTEM
from coding_agent.runtime import Runtime
from coding_agent.trajectories.base import REGISTRY

RULE_THRESHOLD = 1.0
RULE_MARGIN = 0.5


def rule_scores(text: str) -> dict[str, float]:
    t = text.strip().lower()
    scores: dict[str, float] = {}
    for name, traj in REGISTRY.items():
        s = sum(w for pat, w in traj.patterns if re.search(pat, t, re.I | re.M))
        if s:
            scores[name] = s
    return scores


def route(text: str, rt: Runtime) -> dict:
    scores = rule_scores(text)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    if ranked:
        best, top = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        if top >= RULE_THRESHOLD and top - second >= RULE_MARGIN:
            conf = round(min(0.99, 0.6 + 0.1 * (top - second)), 2)
            return {"trajectory": best, "confidence": conf, "source": "rules",
                    "reason": f"pattern scores {dict(ranked)}"}

    options = "\n".join(
        f"- {t.name}: {t.description} e.g. " + "; ".join(f'"{e}"' for e in t.examples[:3])
        for t in REGISTRY.values())
    recent = "\n".join(f"{t.user} → [{t.trajectory}] {t.outcome[:100]}"
                       for t in rt.session.turns[-3:]) or "(none)"
    prompt = (f"Trajectories:\n{options}\n\nRecent turns:\n{recent}\n\n"
              f"Request: {text}\n\nRule pre-scores (may be wrong): {scores or 'none'}")
    try:
        out = rt.llm.complete_json(ROUTER_SYSTEM, prompt, "route", ROUTER_SCHEMA, max_tokens=300)
    except Exception as exc:  # model down → best rule guess, else chat
        best = ranked[0][0] if ranked else "chat"
        return {"trajectory": best, "confidence": 0.3, "source": "fallback",
                "reason": f"model routing failed ({exc}); using best rule guess"}
    if out["trajectory"] not in REGISTRY:
        return {"trajectory": "chat", "confidence": 0.2, "source": "model-invalid",
                "reason": f"model chose unknown trajectory {out['trajectory']!r}"}
    return {"trajectory": out["trajectory"], "confidence": float(out["confidence"]),
            "source": "model", "reason": out["reason"]}
