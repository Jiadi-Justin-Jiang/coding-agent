# Self-assessment against the evaluation rubric

This maps each rubric dimension's level-5 description to the code, the tests that prove it, and the demo session that shows it. Each section ends with the known limitations.

| Dimension | Level-5 description | Where to look |
|---|---|---|
| Orchestration | Clean state machine, handles failures, extensible | `graph.py`, `router.py`, `trajectories/`, `docs/state_graph.png` |
| Memory | Smart retrieval, eviction strategy | `memory/` |
| Feedback | Multi-source, prevents loops, learns | `feedback/`, `trajectories/edit.py`, `trajectories/correct.py` |
| Tool selection | Validated, minimal calls, pluggable | `tools/registry.py`, `mcp_servers/`, `agent.yaml` |
| Code quality | Production-grade, observable | `tests/`, `observability/tracer.py` |

## Orchestration: clean state machine, handles failures, extensible

### Clean state machine

- A LangGraph `StateGraph` with named nodes and explicit edges. Every request follows `route → load_context → <trajectory> → finalize`.
- `docs/state_graph.png` is generated from the compiled graph (`make graph`), so the diagram can't drift from the code.
- Routing is a decision, not a hardcoded path. Pattern rules route the clear cases with no LLM call. The model is asked only when the rules are unsure, and its answer is validated.
- Tests: `test_rules_route_sample_interactions_without_llm`, `test_ambiguous_request_goes_to_model`.

### Handles failures

- Every node is wrapped so an exception becomes `state.error`, which sends the flow to a `recover` node instead of crashing.
- `recover` rolls back half-finished writes using the edit journal and leaves the session usable.
- Two backstops stop a runaway loop: a per-request step budget and LangGraph's `recursion_limit`.
- Tests: `test_tool_failure_mid_edit_rolls_back_partial_writes` (a write fails partway through an edit), `test_model_outage_is_reported_not_crashed`.

### Extensible

- A new trajectory is one module calling `register(Trajectory(...))`. The router, graph and `/help` pick it up with no core changes.

**Known limitation:** there's no LangGraph checkpointer, so a crash of the process itself loses the session. This is listed as a next step in `WRITEUP.md`.

## Memory: smart retrieval, eviction strategy

### Smart retrieval

- `build_context` scores each item by keyword relevance to the request, by recency (exponential decay), and by whether it involves a file currently in play. It fills a token budget (1,200 by default) with the best items.
- Preferences are filtered by trajectory. Lessons are retrieved only when relevant (top 3, above a threshold).
- Tests: `test_context_is_budgeted_and_relevance_ranked`, `test_lessons_retrieved_by_relevance`.

### Eviction strategy

Each kind of data has its own rule, in a deliberate order of loss:

1. Raw command output is parsed into structured failures immediately. The raw text is never kept.
2. Cached file contents are an LRU of 8 files. An evicted file keeps a one-line summary, and a file whose on-disk hash changed is never served from the cache.
3. Turns older than the last 6 are folded into a rolling summary with its own size cap.
4. Lessons are capped at 50, and the least useful are evicted first.

Tests: `test_file_cache_lru_eviction_keeps_summary`, `test_stale_cache_is_never_served`, `test_compaction_folds_old_turns_into_summary`, `test_corrupt_store_is_quarantined`.

**Known limitation:** retrieval is lexical, so it can miss synonyms. This keeps memory dependency-free and explainable. Embeddings are the upgrade path for larger codebases.

## Feedback: multi-source, prevents loops, learns

### Multi-source

- **Linter:** `ruff --fix` runs inside verification before the user sees the result.
- **Tests:** pytest output is parsed into failures at a file and line, then classified as regressions, still-failing targets, or failures that existed before the edit.
- **User:** "undo" and "that's wrong".
- **Preference policies:** "Always add docstrings" is checked against the AST, and a violation forces a self-fix (demo session 2).

### Prevents loops

Three independent brakes:

- an attempt budget (`max_fix_attempts`, default 3)
- no-progress detection (identical failure signature, line numbers normalised out)
- a made-it-worse check (the problem count grew)

When any brake fires, the files are restored to their exact pre-request bytes. Tests: `test_guard_budget_progress_and_regression`, `test_unfixable_edit_is_bounded_and_rolled_back`, `test_bad_patch_triggers_bounded_replan`. Demo session 3 shows it end to end.

### Learns

- "That's wrong" reverts, re-plans with the rejected diff marked "don't repeat this", and stores the feedback as a persistent lesson that later similar requests retrieve.
- Preferences survive into a new process and are enforced there.
- Tests: `test_correction_reverts_replans_and_stores_lesson`, `test_preference_persists_and_is_enforced_next_session`.

**Known limitation:** no type checker is wired in yet. Adding `mypy` as another verification signal would be one command in `agent.yaml` plus a small output parser.

## Tool selection: validated, minimal calls, pluggable

### Validated

- Arguments are checked against each tool's JSON schema before the call.
- Every outcome is a `ToolResult` with an `error_kind` (validation, tool, transport, unknown tool). Nodes never see a raw exception.
- The servers confine paths to the workspace, refuse writes to files that changed underneath (sha256 compare-and-swap), and run only allow-listed commands.
- Tests: `test_sandbox_validation_allowlist_and_cache`, `test_write_conflict_and_dry_run`.

### Minimal calls

- Listing the project takes one call (`test_listing_costs_one_tool_call`).
- Locating the right file takes one search.
- Validating the read cache takes one batched hash call, so unchanged files aren't re-read (`test_cache_avoids_rereading_unchanged_files`).
- Read-only results are cached until anything writes.
- The input-validation scenario takes 11 tool calls and 1 LLM call in steady state. "Run the tests" takes no LLM calls.

### Pluggable

- MCP servers are declared in `agent.yaml` and their tools are discovered at startup.
- Nodes ask for capabilities (`read_file`, `run_command`), and the `capabilities:` map binds them to tools. Swapping in `@modelcontextprotocol/server-filesystem` is a config change.

**Design note:** deterministic code chooses the mechanical tool calls (locate, read, verify) and the LLM is spent on judgement (ambiguous routing, planning, fixing, explaining). This is cheaper and more predictable than routing every tool call through the model, and it can be tested without one.

## Code quality: production-grade, observable

### Observable

- Every tool call, LLM call and routing decision is a trace span. Spans stream live in the CLI and are written to `.agent/traces/<session>.jsonl`.
- `/trace [turn]` shows the span tree for a turn. Routing decisions explain themselves, e.g. `pattern scores {'run': 3.0}`.

### Production-grade

- 45 tests: unit tests, integration tests against the real MCP servers on a temporary copy of the target project, and fault injection.
- Ruff runs clean.
- Configuration lives in `agent.yaml` with environment overrides. The persistent store is written atomically and quarantines a corrupt file.
- The shell server strips `*_KEY`, `*_TOKEN` and `*_SECRET` variables from its environment.
- The deterministic replay LLM makes the tests and demos reproducible.

**Known limitations:**

- The shell allow-list is a guardrail, not a sandbox. Production use should run the agent in a container.
- The demo recordings use the replay LLM. `make demos-live` records the same scripts against a real model (needs `ANTHROPIC_API_KEY`).

## Bonus points

- **Streaming output:** live progress lines as the agent works.
- **Observability traces:** `/trace` in the REPL, plus a JSONL file per session.
- **Dry-run mode:** `--dry-run`, `/dry-run on`, or a `dry run:` prefix on a request.
- **Clean rollback for multi-step edits:** all edits from one request revert as a single transaction, back to the exact original bytes.
