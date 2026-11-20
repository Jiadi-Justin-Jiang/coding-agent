"""Session memory: what happened in *this* conversation.

Holds turns, files read, commands run (with parsed results), and edit
transactions (via the journal). It is bounded:

* file contents are an LRU cache (``max_cached_files``); evicted files keep a
  one-line summary + hash so the agent still "knows" it read them
* stale contents (hash changed on disk) are dropped, never served
* old turns are folded into an extractive rolling summary once there are more
  than ``keep_recent_turns``; the summary itself is capped

``build_context`` is the retrieval API: given the current request it scores
every memory item by relevance + recency (+ pins) and packs the best ones
into a token budget. Nothing is dumped wholesale into the prompt.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field

from coding_agent.memory.text import PATH_RE, estimate_tokens, keywords, overlap


@dataclass
class Turn:
    n: int
    user: str
    trajectory: str
    outcome: str
    files: list[str] = field(default_factory=list)


@dataclass
class FileRecord:
    path: str
    sha256: str
    summary: str
    content: str | None
    last_turn: int


@dataclass
class CommandRecord:
    n: int
    command: str
    kind: str            # test | lint | other
    exit_code: int | None
    summary: str
    fingerprint: str | None = None
    failures: list[dict] = field(default_factory=list)


class SessionMemory:
    def __init__(self, keep_recent_turns: int = 6, max_cached_files: int = 8,
                 summary_char_cap: int = 2500) -> None:
        self.turns: list[Turn] = []
        self.rolling_summary: list[str] = []
        self.files: OrderedDict[str, FileRecord] = OrderedDict()
        self.commands: list[CommandRecord] = []
        self.notes: list[str] = []               # session-scoped feedback
        self.keep_recent = keep_recent_turns
        self.max_files = max_cached_files
        self.summary_cap = summary_char_cap
        self.turn_no = 0
        self.evictions = 0

    # ------------------------------------------------------------------ files
    def remember_file(self, path: str, content: str, sha256: str, summary: str = "") -> None:
        rec = FileRecord(path, sha256, summary or _first_line(content), content, self.turn_no)
        self.files[path] = rec
        self.files.move_to_end(path)
        live = [p for p, r in self.files.items() if r.content is not None]
        while len(live) > self.max_files:      # LRU: drop content, keep the summary
            oldest = live.pop(0)
            self.files[oldest].content = None
            self.evictions += 1

    def cached(self, path: str, sha256: str | None = None) -> FileRecord | None:
        rec = self.files.get(path)
        if rec is None or rec.content is None:
            return None
        if sha256 is not None and rec.sha256 != sha256:
            rec.content = None                 # stale: never serve outdated code
            return None
        self.files.move_to_end(path)
        return rec

    def invalidate(self, path: str) -> None:
        if path in self.files:
            self.files[path].content = None

    # ------------------------------------------------------------------ commands
    def record_command(self, rec: CommandRecord) -> None:
        self.commands.append(rec)
        self.commands = self.commands[-20:]

    def last_command(self, kind: str) -> CommandRecord | None:
        return next((c for c in reversed(self.commands) if c.kind == kind), None)

    def test_result_for(self, fingerprint: str) -> CommandRecord | None:
        return next((c for c in reversed(self.commands)
                     if c.kind == "test" and c.fingerprint == fingerprint), None)

    # ------------------------------------------------------------------ turns
    def start_turn(self) -> int:
        self.turn_no += 1
        return self.turn_no

    def end_turn(self, user: str, trajectory: str, outcome: str, files: list[str]) -> None:
        self.turns.append(Turn(self.turn_no, user, trajectory, outcome, files))
        self.compact()

    def compact(self) -> None:
        while len(self.turns) > self.keep_recent:
            t = self.turns.pop(0)
            files = f" files={','.join(t.files)}" if t.files else ""
            self.rolling_summary.append(
                f"T{t.n} [{t.trajectory}] {t.user[:80]!r} → {t.outcome[:120]}{files}")
        while sum(len(s) for s in self.rolling_summary) > self.summary_cap:
            self.rolling_summary.pop(0)

    # ------------------------------------------------------------------ retrieval
    def build_context(self, query: str, budget_tokens: int = 1500,
                      pinned_paths: tuple[str, ...] = ()) -> str:
        """Score memory items and pack the most useful into ``budget_tokens``."""
        terms = keywords(query)
        mentioned = set(PATH_RE.findall(query)) | set(pinned_paths)
        candidates: list[tuple[float, str]] = []

        def recency(n: int) -> float:
            return math.exp(-(self.turn_no - n) / 4)

        for t in self.turns:
            text = f"T{t.n} user: {t.user} → {t.outcome}"
            score = 0.6 * recency(t.n) + overlap(terms, text) + (0.3 if t.n == self.turn_no - 1 else 0)
            candidates.append((score, text))
        seen: set[tuple] = set()
        for c in reversed(self.commands[-8:]):     # newest first; skip identical repeats
            key = (c.command, c.summary, tuple(f["nodeid"] for f in c.failures))
            if key in seen:
                continue
            seen.add(key)
            fails = "; ".join(f"{f['nodeid']}: {f['message'][:100]}" for f in c.failures[:3])
            text = f"T{c.n} ran `{c.command}` → exit {c.exit_code}: {c.summary}" + (
                f" | failures: {fails}" if fails else "")
            candidates.append((0.5 * recency(c.n) + overlap(terms, text) + 0.2, text))
        for path, rec in self.files.items():
            text = f"read {path} (T{rec.last_turn}): {rec.summary}"
            bonus = 1.0 if path in mentioned else 0
            candidates.append((0.3 * recency(rec.last_turn) + overlap(terms, text) + bonus, text))
        for note in self.notes[-5:]:
            candidates.append((1.0 + overlap(terms, note), f"user feedback: {note}"))

        # Summarised (old) turns compete like everything else: low base score,
        # so they only win budget when they are relevant to the current request.
        for line in self.rolling_summary:
            candidates.append((0.2 + overlap(terms, line), "(earlier) " + line))

        candidates.sort(key=lambda c: c[0], reverse=True)
        out: list[str] = []
        used = 0
        for score, text in candidates:
            if score < 0.15:
                break
            cost = estimate_tokens(text)
            if used + cost > budget_tokens:
                continue
            out.append("- " + text)
            used += cost
        return "\n".join(out)

    def stats(self) -> dict:
        return {"turns": self.turn_no, "recent_turns": len(self.turns),
                "summarised_turns": len(self.rolling_summary),
                "files_known": len(self.files),
                "files_cached": sum(1 for r in self.files.values() if r.content is not None),
                "evictions": self.evictions, "commands": len(self.commands)}


def _first_line(content: str) -> str:
    for line in content.splitlines():
        s = line.strip().strip('"#').strip()
        if s:
            return s[:100]
    return ""
