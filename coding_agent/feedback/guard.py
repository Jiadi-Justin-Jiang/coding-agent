"""Loop prevention for self-correction.

Three independent brakes, any of which stops a fix loop:
1. attempt budget per request (``max_attempts``)
2. no-progress detection: the same failure signature seen twice
3. regression detection: the failure set grew after a "fix"
(plus LangGraph's global ``recursion_limit`` as the last-resort backstop)
"""

from __future__ import annotations


def should_retry(attempts: int, max_attempts: int, history: list[dict], current: dict) -> tuple[bool, str]:
    if attempts >= max_attempts:
        return False, f"attempt budget exhausted ({attempts}/{max_attempts})"
    if any(h["signature"] == current["signature"] for h in history):
        return False, "no progress: identical failures after a fix attempt"
    if history and current["count"] > history[-1]["count"] + 1:
        return False, f"fix made things worse ({history[-1]['count']} → {current['count']} problems)"
    return True, "retrying with failure feedback"
