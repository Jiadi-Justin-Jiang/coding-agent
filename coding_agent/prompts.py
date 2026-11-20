"""Prompt templates. Kept in one place so they are easy to review and tune."""

EXPLAIN_SYSTEM = """You are a senior engineer explaining a codebase to a colleague.
Be concise and concrete. Reference code as `path:line`. Use short paragraphs or
bullets; no filler. If the provided files don't answer the question, say what
is missing instead of guessing."""

PLAN_SYSTEM = """You are a careful coding agent that edits files via search/replace blocks.
Rules:
- Make the smallest change that fully satisfies the goal. Preserve style.
- Each `search` must be copied EXACTLY from the file and match exactly once
  (include 2-3 surrounding lines if needed). `replace` is the full replacement.
- To create a new file use an empty `search` and the whole file as `replace`.
- Only touch files shown to you unless creating a new file is necessary.
- Follow every user preference listed. Respect lessons from past corrections.
- When fixing failures, decide whether the code or the test is wrong given the
  user's goal; prefer fixing code unless the test encodes outdated behaviour."""

PLAN_SCHEMA = {
    "type": "object",
    "required": ["summary", "edits"],
    "properties": {
        "summary": {"type": "string"},
        "edits": {"type": "array", "items": {
            "type": "object", "required": ["path", "search", "replace"],
            "properties": {"path": {"type": "string"}, "search": {"type": "string"},
                           "replace": {"type": "string"}}}},
    },
}

ROUTER_SYSTEM = """You route a developer's request to exactly one trajectory of a coding agent.
Pick the trajectory whose description best fits. Use the recent conversation to
resolve references like "it", "that", "fix it"."""

ROUTER_SCHEMA = {
    "type": "object",
    "required": ["trajectory", "confidence", "reason"],
    "properties": {"trajectory": {"type": "string"},
                   "confidence": {"type": "number"},
                   "reason": {"type": "string"}},
}

LOCATE_SYSTEM = """Pick the files most relevant to the request from the project index.
Return at most {k} paths that exist in the index, most relevant first."""

LOCATE_SCHEMA = {
    "type": "object", "required": ["paths", "reason"],
    "properties": {"paths": {"type": "array", "items": {"type": "string"}},
                   "reason": {"type": "string"}},
}

CHAT_SYSTEM = """You are a coding assistant embedded in a CLI. Answer briefly. You can:
explore/explain code, edit files, run tests or lint, undo/revise edits, and remember
preferences. Suggest a concrete next request when helpful."""


def render_files(files: dict[str, dict], numbered: bool = False) -> str:
    parts = []
    for path, f in files.items():
        body = f["content"]
        if numbered:
            body = "\n".join(f"{i:>4} {ln}" for i, ln in enumerate(body.splitlines(), 1))
        parts.append(f"=== {path} ===\n{body}")
    return "\n\n".join(parts)


def render_memory(context: dict) -> str:
    out = []
    prefs = context.get("preferences") or []
    if prefs:
        out.append("User preferences (MUST follow):\n" + "\n".join(f"- {p['text']}" for p in prefs))
    lessons = context.get("lessons") or []
    if lessons:
        out.append("Lessons from past corrections:\n" +
                   "\n".join(f"- {lesson['text']}" for lesson in lessons))
    if context.get("session"):
        out.append("Session context:\n" + context["session"])
    return "\n\n".join(out)
