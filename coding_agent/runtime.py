"""Runtime: the services every node can use (kept out of graph state)."""

from __future__ import annotations

from dataclasses import dataclass

from coding_agent.config import Config
from coding_agent.edits.journal import EditJournal
from coding_agent.llm.base import LLM
from coding_agent.memory.persistent import PersistentMemory
from coding_agent.memory.session import SessionMemory
from coding_agent.observability.tracer import Tracer
from coding_agent.tools.registry import ToolRegistry


@dataclass
class Runtime:
    config: Config
    tools: ToolRegistry
    llm: LLM
    tracer: Tracer
    session: SessionMemory
    persistent: PersistentMemory
    journal: EditJournal

    def status(self, message: str) -> None:
        """Human-readable progress line (streamed by the CLI)."""
        self.tracer.event("status", message)

    @property
    def dry_run(self) -> bool:
        return bool(self.config.raw.get("dry_run"))
