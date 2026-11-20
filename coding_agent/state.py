"""Graph state = WORKING MEMORY.

Everything here lives for exactly one user request and is discarded after.
Anything worth keeping is promoted to session memory in ``finalize`` (and to
persistent memory only by the remember/correct trajectories).
"""

from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    user_input: str
    goal: str                      # cleaned task statement (may be rewritten by correct/run)
    trajectory: str
    route: dict                    # {trajectory, confidence, source, reason}
    flags: dict                    # dry_run, wants_fix, listing, undo ...
    context: dict                  # retrieved memory for this request
    targets: list[str]             # files we are working on
    files: dict[str, dict]         # path -> {content, sha256}
    listing: list[dict]
    plan: dict                     # last model plan {summary, edits}
    patch_error: str | None
    txn_id: str | None             # open edit transaction (journal)
    baseline_fp: str | None        # workspace fingerprint before our first write
    fix_targets: list[str]         # failing tests this request must make pass
    attempts: dict[str, int]
    history: list[dict]            # verification signatures (loop detection)
    verification: dict
    report: dict                   # parsed command output
    response: str
    status: str                    # success | failed | rolled_back | dry_run | info
    error: dict | None
    steps: int
    extra: dict[str, Any]
