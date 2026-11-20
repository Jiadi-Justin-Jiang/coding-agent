"""Edit journal: transactional, multi-file edits with clean rollback.

Every user request that changes code opens one transaction. All writes in
that request — the initial edit *and* every auto-fix attempt — go into the
same transaction, so "undo" restores the exact pre-request state in one step.

Rollback uses optimistic concurrency: a file is only restored if its current
hash still equals what the agent wrote. If the user edited it in the meantime
the file is left alone and reported as a conflict instead of being clobbered.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class FileChange:
    path: str
    before: str | None      # None => file was created by the agent
    after: str


@dataclass
class Transaction:
    id: str
    turn: int
    goal: str
    changes: dict[str, FileChange] = field(default_factory=dict)
    status: str = "open"    # open | committed | rolled_back
    note: str = ""

    def record(self, path: str, before: str | None, after: str) -> None:
        if path in self.changes:          # keep the ORIGINAL before-image
            self.changes[path].after = after
        else:
            self.changes[path] = FileChange(path, before, after)

    @property
    def files(self) -> list[str]:
        return list(self.changes)


class EditJournal:
    def __init__(self) -> None:
        self.transactions: list[Transaction] = []

    def begin(self, turn: int, goal: str) -> Transaction:
        txn = Transaction(uuid.uuid4().hex[:6], turn, goal)
        self.transactions.append(txn)
        return txn

    def get(self, txn_id: str | None) -> Transaction | None:
        return next((t for t in self.transactions if t.id == txn_id), None)

    def open_transactions(self) -> list[Transaction]:
        return [t for t in self.transactions if t.status == "open"]

    def last_committed(self) -> Transaction | None:
        return next((t for t in reversed(self.transactions)
                     if t.status == "committed" and t.changes), None)

    def rollback(self, txn: Transaction, tools) -> dict:
        """Restore every file in ``txn``. Returns {restored, conflicts, errors}."""
        restored, conflicts, errors = [], [], []
        for change in reversed(list(txn.changes.values())):
            expected = sha256(change.after)
            if change.before is None:
                res = tools.call("delete_file", path=change.path, expected_sha256=expected)
            else:
                res = tools.call("write_file", path=change.path, content=change.before,
                                 expected_sha256=expected)
            if res.ok:
                restored.append(change.path)
            elif "conflict" in (res.error or ""):
                conflicts.append(change.path)
            else:
                errors.append(f"{change.path}: {res.error}")
        txn.status = "rolled_back" if not (conflicts or errors) else "partially_rolled_back"
        return {"restored": restored, "conflicts": conflicts, "errors": errors}
