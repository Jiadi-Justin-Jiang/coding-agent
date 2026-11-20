"""Tiny lexical utilities used for memory retrieval and file location."""

from __future__ import annotations

import re

STOPWORDS = set("""
a an the to of in on for and or is are was be it this that these those me my i you we
what how does do show tell explain where which who why can could would should please
add change fix make let lets use using with from into about file files code project
there here all any some them it's its just also now then than when will want need
""".split())


def stem(word: str) -> str:
    """Crude suffix stripping, applied until stable ("docstrings" → "docstr")."""
    changed = True
    while changed:
        changed = False
        for suf in ("ation", "ing", "ed", "es", "s"):
            if len(word) > len(suf) + 3 and word.endswith(suf):
                word, changed = word[: -len(suf)], True
                break
    return word


def keywords(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]+", text.lower())
    out: list[str] = []
    for w in words:
        for part in w.split("_"):
            if len(part) > 2 and part not in STOPWORDS:
                s = stem(part)
                if s not in out:
                    out.append(s)
    return out


def overlap(query_terms: list[str], text: str) -> float:
    if not query_terms:
        return 0.0
    terms = set(keywords(text))

    def hit(t: str) -> bool:  # tolerate stemming differences: "titl" ~ "title"
        return t in terms or any(len(min(t, w, key=len)) >= 4 and
                                 (w.startswith(t) or t.startswith(w)) for w in terms)
    return sum(1 for t in query_terms if hit(t)) / len(query_terms)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


PATH_RE = re.compile(r"[\w./-]+\.(?:py|md|toml|txt|yaml|yml|json|cfg|ini)\b")
