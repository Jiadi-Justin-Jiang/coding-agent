"""Shared node building blocks: locating and reading files."""

from __future__ import annotations

import math
import re

from coding_agent.memory.text import PATH_RE, keywords
from coding_agent.prompts import LOCATE_SCHEMA, LOCATE_SYSTEM
from coding_agent.runtime import Runtime

DEF_RE = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s")


def project_index(rt: Runtime) -> list[dict]:
    res = rt.tools.call("list_directory", path=".", recursive=True, include_summary=True)
    if not res.ok:
        return []
    return [e for e in res.data["entries"] if e["type"] == "file"]


def locate(rt: Runtime, goal: str, k: int, hints: list[str] | None = None,
           extra_terms: list[str] | None = None,
           include_tests: bool = True,
           drop_terms: tuple[str, ...] = ()) -> tuple[list[str], str]:
    """Find the files a request is about, using as few tool calls as possible.

    1. explicit paths in the request / hints from memory (0 calls)
    2. ONE regex search over the code for all keywords; score files by keyword
       co-occurrence, with a bonus for definitions and filename hits (1-2 calls)
    3. only if that is ambiguous, ask the model to choose from the index (1 LLM call)
    """
    index = project_index(rt)
    known = {e["path"] for e in index}
    chosen: list[str] = []
    for p in PATH_RE.findall(goal) + list(hints or []):
        p = p.lstrip("./")
        if p in known and p not in chosen:
            chosen.append(p)
    terms = (extra_terms or []) + [t for t in keywords(goal) if t not in (drop_terms or ())]
    terms = list(dict.fromkeys(terms))[:8]
    if len(chosen) >= k or not terms:
        return chosen[:k], "explicit paths / memory hints"

    # Score = sum over matching lines of IDF-weighted term hits. Rare terms
    # ("paginate") dominate common ones ("task" in a task app); definitions
    # weigh 3x; terms from memory hints (e.g. failing test names) weigh 2x.
    scores: dict[str, float] = {}
    res = rt.tools.call("search_code", pattern="|".join(re.escape(t) for t in terms),
                        regex=True, glob="*.py", max_results=300)
    matches = res.data.get("matches", []) if res.ok else []
    df = {t: sum(1 for m in matches if t in m["text"].lower()) for t in terms}
    idf = {t: math.log(1 + len(matches) / df[t]) if df[t] else 0 for t in terms}
    boost = {t: 2.0 if t in (extra_terms or []) else 1.0 for t in terms}
    for m in matches:
        line = m["text"].lower()
        w = sum(idf[t] * boost[t] for t in terms if t in line)
        scores[m["path"]] = scores.get(m["path"], 0) + w * (3 if DEF_RE.match(m["text"]) else 1)
    for path in known:
        name = path.rsplit("/", 1)[-1].lower()
        scores[path] = scores.get(path, 0) + 3 * sum(idf.get(t, 1) for t in terms if t in name)
    if not include_tests and "test" not in goal.lower():
        scores = {p: v * 0.5 if "test" in p else v for p, v in scores.items()}
    ranked = sorted((p for p in scores if scores[p] > 0 and p not in chosen),
                    key=lambda p: (-scores[p], "test" in p, p))
    reason = "keyword search: " + ", ".join(f"{p}={scores[p]:.0f}" for p in ranked[:4])

    ambiguous = not ranked or not matches
    if ambiguous and index:
        listing = "\n".join(f"{e['path']}: {e.get('summary', '')}" for e in index)
        try:
            out = rt.llm.complete_json(
                LOCATE_SYSTEM.format(k=k), f"Request: {goal}\n\nProject index:\n{listing}",
                "locate", LOCATE_SCHEMA)
            picked = [p for p in out["paths"] if p in known]
            if picked:
                return list(dict.fromkeys(chosen + picked))[:k], "model: " + out["reason"]
        except Exception as exc:  # fall back to lexical ranking
            reason += f" (model locate failed: {exc})"
    return (chosen + ranked)[:k], reason


def read_files(rt: Runtime, paths: list[str]) -> tuple[dict[str, dict], list[str]]:
    """Read files, reusing session-cached content when the on-disk hash is unchanged.

    One batched ``stat_files`` call validates the whole cache; only changed or
    unseen files are actually read.
    """
    files: dict[str, dict] = {}
    missing: list[str] = []
    if not paths:
        return files, missing
    stat = rt.tools.call("stat_files", paths=paths)
    info = {f["path"]: f for f in stat.data.get("files", [])} if stat.ok else {}
    for p in paths:
        meta = info.get(p, {})
        if stat.ok and not meta.get("exists"):
            missing.append(p)
            continue
        rec = rt.session.cached(p, meta.get("sha256"))
        if rec is not None:
            rt.tracer.event("memory", f"cache hit {p}", summary="skipped read (unchanged)")
            files[p] = {"content": rec.content, "sha256": rec.sha256}
            continue
        res = rt.tools.call("read_file", path=p)
        if not res.ok:
            missing.append(p)
            continue
        files[p] = {"content": res.data["content"], "sha256": res.data["sha256"]}
        rt.session.remember_file(p, res.data["content"], res.data["sha256"])
    return files, missing


def test_file_for(path: str, known: set[str]) -> str | None:
    stem = path.rsplit("/", 1)[-1].removesuffix(".py")
    for cand in (f"tests/test_{stem}.py", f"test_{stem}.py"):
        if cand in known:
            return cand
    return None
