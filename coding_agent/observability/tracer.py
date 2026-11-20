"""Structured tracing: every node, tool call, LLM call and routing decision.

Spans are appended to ``<workspace>/.agent/traces/<session>.jsonl`` and also
pushed to an optional live listener (the CLI uses it to stream progress).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Span:
    kind: str            # node | tool | llm | decision | memory | feedback
    name: str
    turn: int
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    parent: str | None = None
    start: float = field(default_factory=time.time)
    duration_ms: int | None = None
    ok: bool = True
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


class Tracer:
    def __init__(self, session_id: str, trace_dir: Path | None = None) -> None:
        self.session_id = session_id
        self.turn = 0
        self.spans: list[Span] = []
        self._stack: list[str] = []
        self.listeners: list[Callable[[str, Span], None]] = []
        self.path = None
        if trace_dir is not None:
            trace_dir.mkdir(parents=True, exist_ok=True)
            self.path = trace_dir / f"{session_id}.jsonl"

    def new_turn(self) -> int:
        self.turn += 1
        return self.turn

    def _emit(self, phase: str, span: Span) -> None:
        for fn in self.listeners:
            try:
                fn(phase, span)
            except Exception:  # a broken listener must never break the agent
                pass

    @contextmanager
    def span(self, kind: str, name: str, **attrs: Any):
        s = Span(kind=kind, name=name, turn=self.turn,
                 parent=self._stack[-1] if self._stack else None, attrs=dict(attrs))
        self._stack.append(s.span_id)
        self._emit("start", s)
        t0 = time.monotonic()
        try:
            yield s
        except Exception as exc:
            s.ok = False
            s.attrs["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            s.duration_ms = int((time.monotonic() - t0) * 1000)
            self._stack.pop()
            self._record(s)
            self._emit("end", s)

    def event(self, kind: str, name: str, **attrs: Any) -> Span:
        """Point-in-time record (e.g. a routing decision)."""
        s = Span(kind=kind, name=name, turn=self.turn, duration_ms=0,
                 parent=self._stack[-1] if self._stack else None, attrs=dict(attrs))
        self._record(s)
        self._emit("event", s)
        return s

    def _record(self, s: Span) -> None:
        self.spans.append(s)
        if self.path:
            with self.path.open("a") as fh:
                fh.write(json.dumps(s.to_dict(), default=str) + "\n")

    def turn_spans(self, turn: int | None = None) -> list[Span]:
        t = self.turn if turn is None else turn
        return [s for s in self.spans if s.turn == t]

    def stats(self, turn: int | None = None) -> dict[str, int]:
        spans = self.turn_spans(turn)
        return {
            "tool_calls": sum(1 for s in spans if s.kind == "tool" and not s.attrs.get("cached")),
            "cached_calls": sum(1 for s in spans if s.kind == "tool" and s.attrs.get("cached")),
            "llm_calls": sum(1 for s in spans if s.kind == "llm"),
            "nodes": sum(1 for s in spans if s.kind == "node"),
        }

    def render_tree(self, turn: int | None = None) -> str:
        spans = sorted(self.turn_spans(turn), key=lambda s: s.start)
        children: dict[str | None, list[Span]] = {}
        for s in spans:
            children.setdefault(s.parent, []).append(s)
        lines: list[str] = []

        def walk(parent: str | None, depth: int) -> None:
            for s in children.get(parent, []):
                mark = "✓" if s.ok else "✗"
                detail = str(s.attrs.get("summary") or s.attrs.get("reason") or "")[:90]
                if s.attrs.get("cached"):
                    detail += " (cached)"
                if s.attrs.get("error"):
                    detail += f" ERROR: {str(s.attrs['error'])[:80]}"
                dur = f"{s.duration_ms}ms" if s.duration_ms else ""
                lines.append(f"{'  ' * depth}{mark} [{s.kind}] {s.name} {dur} {detail}".rstrip())
                walk(s.span_id, depth + 1)

        walk(None, 0)
        return "\n".join(lines)
