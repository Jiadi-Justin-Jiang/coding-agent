"""Fallback conversational trajectory (greetings, meta questions, unclear requests)."""

from __future__ import annotations

from coding_agent.prompts import CHAT_SYSTEM, render_memory
from coding_agent.trajectories.base import FINALIZE, GraphBuilder, Trajectory, register


def chat_answer(state, rt):
    prompt = f"{render_memory(state.get('context') or {})}\n\nUser: {state['goal']}"
    return {"status": "info", "response": rt.llm.complete(CHAT_SYSTEM, prompt, "chat")}


def build(b: GraphBuilder) -> None:
    b.node("chat_answer", chat_answer)
    b.edge("chat_answer", FINALIZE)


register(Trajectory(
    name="chat",
    description="Anything else: greetings, questions about the agent itself, unclear requests.",
    examples=["hi", "what can you do?"],
    patterns=[(r"^(hi|hello|hey|thanks|thank you|help)\b", 1.5)],
    entry="chat_answer", build=build))
