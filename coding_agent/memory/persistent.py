"""Persistent memory: survives across sessions.

Two kinds of entries, deliberately separate:

* **preferences** — explicit user instructions ("always add docstrings").
  High trust, always injected for matching scopes, optionally backed by an
  enforceable *policy check* (see ``feedback/policies.py``).
* **lessons** — learned from corrections ("that's wrong — use 422").
  Lower trust, retrieved only when relevant to the current goal, and they
  decay out (capped list, least-recently-useful evicted).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path

from coding_agent.memory.text import keywords, overlap

SCOPES = ("edit", "explore", "run", "all")
MAX_LESSONS = 50

# keyword → policy check id (enforced after edits, not just prompted)
POLICY_TRIGGERS = {
    "docstring": "require_docstrings",
    "type hint": "require_type_hints",
    "type annotation": "require_type_hints",
}


class PersistentMemory:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.data = {"preferences": [], "lessons": []}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text()))
            except json.JSONDecodeError:
                # never crash on a corrupt store; keep a backup and start fresh
                self.path.rename(self.path.with_suffix(".corrupt.json"))

    # ------------------------------------------------------------------ io
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(self.data, fh, indent=2)
        os.replace(tmp, self.path)  # atomic

    # ------------------------------------------------------------------ preferences
    @property
    def preferences(self) -> list[dict]:
        return self.data["preferences"]

    @property
    def lessons(self) -> list[dict]:
        return self.data["lessons"]

    def add_preference(self, text: str, scope: str = "all") -> tuple[dict, bool]:
        """Returns (entry, created). Near-duplicates update instead of piling up."""
        text = text.strip().rstrip(".")
        terms = keywords(text)
        for pref in self.preferences:
            if overlap(terms, pref["text"]) >= 0.8 and overlap(keywords(pref["text"]), text) >= 0.8:
                pref.update(text=text, scope=scope, updated_at=time.time())
                self.save()
                return pref, False
        check = next((c for k, c in POLICY_TRIGGERS.items() if k in text.lower()), None)
        entry = {"id": uuid.uuid4().hex[:6], "text": text, "scope": scope,
                 "check": check, "created_at": time.time()}
        self.preferences.append(entry)
        self.save()
        return entry, True

    def remove_preference(self, needle: str) -> list[dict]:
        terms = [t for t in keywords(needle) if t not in ("preference", "prefer", "rule", "setting")]
        removed = [p for p in self.preferences
                   if p["id"] == needle or (terms and overlap(terms, p["text"]) >= 0.5)]
        self.data["preferences"] = [p for p in self.preferences if p not in removed]
        self.save()
        return removed

    def preferences_for(self, trajectory: str) -> list[dict]:
        return [p for p in self.preferences if p["scope"] in ("all", trajectory)]

    def policy_checks(self) -> list[str]:
        return sorted({p["check"] for p in self.preferences if p.get("check")})

    # ------------------------------------------------------------------ lessons
    def add_lesson(self, text: str, context: str = "") -> dict:
        terms = keywords(text + " " + context)
        for lesson in self.lessons:
            if overlap(terms, " ".join(lesson["keywords"])) >= 0.7:
                lesson["count"] += 1
                lesson["last_seen"] = time.time()
                self.save()
                return lesson
        entry = {"id": uuid.uuid4().hex[:6], "text": text, "keywords": terms[:12],
                 "count": 1, "last_seen": time.time()}
        self.lessons.append(entry)
        if len(self.lessons) > MAX_LESSONS:  # evict least recently useful
            self.lessons.sort(key=lambda lesson: (lesson["count"], lesson["last_seen"]))
            del self.lessons[0]
        self.save()
        return entry

    def relevant_lessons(self, query: str, k: int = 3, threshold: float = 0.25) -> list[dict]:
        terms = keywords(query)
        scored = [(overlap(terms, " ".join(lesson["keywords"])), lesson) for lesson in self.lessons]
        scored = [s for s in scored if s[0] >= threshold]
        scored.sort(key=lambda s: (s[0], s[1]["count"]), reverse=True)
        for _, lesson in scored[:k]:
            lesson["last_seen"] = time.time()
        return [lesson for _, lesson in scored[:k]]
