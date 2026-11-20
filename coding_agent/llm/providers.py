"""Concrete LLM providers: Anthropic, OpenAI, and a deterministic replay LLM."""

from __future__ import annotations

import inspect
import os
import re
from pathlib import Path

import yaml

from coding_agent.llm.base import LLM, LLMError
from coding_agent.observability.tracer import Tracer


class AnthropicLLM(LLM):
    name = "anthropic"

    def __init__(self, tracer: Tracer, model: str) -> None:
        super().__init__(tracer)
        import anthropic
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMError("ANTHROPIC_API_KEY is not set")
        self.client = anthropic.Anthropic()
        self.model = model
        # anthropic SDK 2.x removed sampling params (temperature, top_p, top_k) from
        # messages.create(); older SDKs still accept them. Only send what the
        # installed SDK understands so both work.
        self._sampling = ({"temperature": 0}
                          if _accepts_kwarg(self.client.messages.create, "temperature") else {})

    def _generate(self, system, prompt, purpose, max_tokens):
        try:
            msg = self.client.messages.create(
                model=self.model, max_tokens=max_tokens, **self._sampling,
                system=system, messages=[{"role": "user", "content": prompt}])
        except Exception as exc:
            raise LLMError(f"anthropic call failed: {exc}") from exc
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _accepts_kwarg(fn, name: str) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


class OpenAILLM(LLM):
    name = "openai"

    def __init__(self, tracer: Tracer, model: str) -> None:
        super().__init__(tracer)
        import openai
        if not os.environ.get("OPENAI_API_KEY"):
            raise LLMError("OPENAI_API_KEY is not set")
        self.client = openai.OpenAI()
        self.model = model

    def _generate(self, system, prompt, purpose, max_tokens):
        try:
            resp = self.client.chat.completions.create(
                model=self.model, max_tokens=max_tokens, temperature=0,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": prompt}])
        except Exception as exc:
            raise LLMError(f"openai call failed: {exc}") from exc
        return resp.choices[0].message.content or ""


class ReplayLLM(LLM):
    """Deterministic LLM for tests and reproducible demos.

    A fixture file holds an ordered list of rules::

        - purpose: edit.plan          # exact purpose or prefix
          match: "empty task titles"  # optional regex against the prompt
          once: true                  # consume after first use (default true)
          response: |
            {...}

    The first unconsumed rule whose purpose and regex match wins. Everything
    *around* the model — routing, tools, memory, verification, rollback — runs
    for real; only the text the model would have produced is canned.
    """

    name = "replay"

    def __init__(self, tracer: Tracer, rules: list[dict] | None = None,
                 fixture: str | Path | None = None) -> None:
        super().__init__(tracer)
        self.rules = list(rules or [])
        if fixture:
            self.rules.extend(yaml.safe_load(Path(fixture).read_text()) or [])
        self.used: set[int] = set()
        self.calls: list[tuple[str, str]] = []

    def add(self, purpose: str, response: str, match: str | None = None, once: bool = True):
        self.rules.append({"purpose": purpose, "response": response,
                           "match": match, "once": once})

    def _generate(self, system, prompt, purpose, max_tokens):
        self.calls.append((purpose, prompt))
        for i, rule in enumerate(self.rules):
            if i in self.used:
                continue
            p = rule["purpose"]
            if not (purpose == p or purpose.startswith(p + ":") or
                    (p.endswith("*") and purpose.startswith(p[:-1]))):
                continue
            if rule.get("match") and not re.search(rule["match"], prompt, re.S | re.I):
                continue
            if rule.get("once", True):
                self.used.add(i)
            return rule["response"]
        raise LLMError(f"replay: no fixture response for purpose '{purpose}'")


def build_llm(cfg: dict, tracer: Tracer) -> LLM:
    provider = os.environ.get("AGENT_LLM", cfg.get("provider", "anthropic"))
    model = os.environ.get("AGENT_MODEL", cfg.get("model", "claude-sonnet-4-5"))
    if provider == "anthropic":
        return AnthropicLLM(tracer, model)
    if provider == "openai":
        return OpenAILLM(tracer, os.environ.get("AGENT_MODEL", cfg.get("openai_model", "gpt-4.1")))
    if provider == "replay":
        fixture = os.environ.get("AGENT_REPLAY", cfg.get("replay_file"))
        return ReplayLLM(tracer, fixture=fixture)
    raise LLMError(f"unknown LLM provider '{provider}'")
