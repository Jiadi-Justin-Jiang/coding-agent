"""Public facade: build services, run one request through the graph."""

from __future__ import annotations

import time
from datetime import datetime

from coding_agent.config import Config
from coding_agent.edits.journal import EditJournal
from coding_agent.graph import build_graph
from coding_agent.llm.base import LLM
from coding_agent.llm.providers import build_llm
from coding_agent.memory.persistent import PersistentMemory
from coding_agent.memory.session import SessionMemory
from coding_agent.observability.tracer import Tracer
from coding_agent.runtime import Runtime
from coding_agent.tools.registry import ToolRegistry


class CodingAgent:
    def __init__(self, config: Config, llm: LLM | None = None, trace: bool = True) -> None:
        session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.tracer = Tracer(session_id, config.base_dir / ".agent" / "traces" if trace else None)
        self.tools = ToolRegistry(config.server_configs(), self.tracer,
                                  config.raw.get("capabilities")).start()
        if llm is not None:
            llm.tracer = self.tracer
        mem = config.memory
        self.rt = Runtime(
            config=config, tools=self.tools, llm=llm or build_llm(config.raw["llm"], self.tracer),
            tracer=self.tracer,
            session=SessionMemory(mem["keep_recent_turns"], mem["max_cached_files"]),
            persistent=PersistentMemory(mem["persistent_path"]), journal=EditJournal())
        self.graph = build_graph(self.rt)

    def run(self, text: str) -> dict:
        self.tracer.new_turn()
        self.rt.session.start_turn()
        t0 = time.monotonic()
        state = {"user_input": text, "attempts": {}, "history": [], "flags": {}, "extra": {}}
        final: dict = {}
        for snapshot in self.graph.stream(state, {"recursion_limit": 120}, stream_mode="values"):
            final = snapshot
        return {"response": final.get("response", ""), "status": final.get("status"),
                "trajectory": final.get("trajectory"), "route": final.get("route"),
                "elapsed_s": round(time.monotonic() - t0, 2), **self.tracer.stats()}

    def close(self) -> None:
        self.tools.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
