"""Provider-agnostic LLM interface.

Nodes only ever call ``complete`` (free text) or ``complete_json`` (structured,
validated, with one automatic repair round-trip). Every call is traced with a
``purpose`` label so traces show *why* the model was consulted.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

import jsonschema

from coding_agent.observability.tracer import Tracer


class LLMError(RuntimeError):
    pass


class LLM(ABC):
    name = "base"

    def __init__(self, tracer: Tracer) -> None:
        self.tracer = tracer

    @abstractmethod
    def _generate(self, system: str, prompt: str, purpose: str, max_tokens: int) -> str:
        ...

    def complete(self, system: str, prompt: str, purpose: str, max_tokens: int = 1500) -> str:
        with self.tracer.span("llm", purpose, provider=self.name,
                              prompt_chars=len(prompt)) as span:
            out = self._generate(system, prompt, purpose, max_tokens)
            span.attrs["output_chars"] = len(out)
            return out

    def complete_json(self, system: str, prompt: str, purpose: str,
                      schema: dict[str, Any], max_tokens: int = 3000) -> dict:
        sys_prompt = (system + "\n\nRespond with ONLY a JSON object matching this schema, "
                      "no prose, no code fences:\n" + json.dumps(schema))
        text = self.complete(sys_prompt, prompt, purpose, max_tokens)
        try:
            return _validate(text, schema)
        except (ValueError, jsonschema.ValidationError) as exc:
            # One repair attempt with the exact error, then give up (no loops).
            repair = (f"{prompt}\n\nYour previous reply was invalid: {exc}.\n"
                      f"Previous reply:\n{text[:2000]}\nReturn corrected JSON only.")
            text = self.complete(sys_prompt, repair, purpose + ":repair", max_tokens)
            try:
                return _validate(text, schema)
            except (ValueError, jsonschema.ValidationError) as exc2:
                raise LLMError(f"model returned invalid JSON for {purpose}: {exc2}") from exc2


def _validate(text: str, schema: dict) -> dict:
    data = extract_json(text)
    jsonschema.validate(data, schema)
    return data


def extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object found")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON: {exc}") from exc
