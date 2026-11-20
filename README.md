# coding-agent

An AI coding assistant that reads, explains, edits, tests and repairs a codebase. It's built on a **LangGraph** state machine, **MCP** tool servers, tiered **memory**, and **feedback loops** (lint, tests and user corrections) that are bounded so they can't run forever.

- **Design writeup** (answers the five brief questions): [`docs/WRITEUP.md`](docs/WRITEUP.md)
- **Rubric self-assessment** (each rubric dimension → code, tests, demo): [`docs/RUBRIC.md`](docs/RUBRIC.md)
- **Diagrams:** [`docs/architecture.png`](docs/architecture.png) (components) · [`docs/state_graph.png`](docs/state_graph.png) (the actual compiled graph)
- **Demo recordings:** [`demos/recordings/`](demos/recordings) (`.cast` for asciinema, `.txt` transcripts)

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .                      # or: pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...       # or AGENT_LLM=openai + OPENAI_API_KEY (pip install -e .[openai])
coding-agent                          # REPL against ./target_project (python -m coding_agent.cli also works)
```

There's nothing else to start. The agent spawns its two MCP servers (`coding_agent/mcp_servers/`) as stdio subprocesses and discovers their tools.

> **anthropic SDK 2.x:** SDK 2.x removed `temperature` from `messages.create()`. The provider detects this and sends `temperature=0` only on older SDKs, so both versions work.

### Command-line flags

Flags go on the **shell** command line, not at the `>` prompt. Typing `--dry-run` inside the REPL only prints a reminder.

```bash
coding-agent --dry-run                       # plan and show diffs, never write
coding-agent -c "run the tests"              # one-shot: run one request and exit
coding-agent --workspace path/to/other/repo  # point it elsewhere
coding-agent -v                              # also stream every graph node
coding-agent --llm replay --replay-file demos/fixtures/session1.yaml   # offline, no API key
coding-agent --script demos/session1.txt     # run a file of requests in order
```

Inside the REPL, use `/dry-run on|off` or start a request with `dry run:` instead.

**Configuration** lives in `agent.yaml`: workspace, LLM provider and model, MCP servers, capability→tool mapping, test and lint commands, limits, memory budgets. Environment overrides: `AGENT_LLM`, `AGENT_MODEL`, `AGENT_MEMORY_PATH`.

### REPL meta-commands

| Command | What it does |
|---|---|
| `/help` | List trajectories with examples |
| `/tools` | List the discovered MCP tools |
| `/prefs` | Show stored preferences and learned lessons |
| `/forget <id>` | Remove a preference |
| `/memory [query]` | Show session stats and what retrieval would return |
| `/trace [turn]` | Span tree of the tool calls and decisions behind a turn |
| `/dry-run on\|off` | Toggle dry-run mode |
| `/quit` (or Ctrl-D) | Exit |

You can also prefix any request with `dry run:` to preview it.

## Running it live: step by step

Edits are written straight into the workspace. To keep the planted bugs in `target_project/` for the next run, point the agent at a throwaway copy and a throwaway memory file:

```bash
source .venv/bin/activate
export ANTHROPIC_API_KEY=sk-...
rm -rf /tmp/tp && cp -r target_project /tmp/tp
export AGENT_MEMORY_PATH=/tmp/agent-memory.json   # fresh preferences; unset to use ~/.coding_agent/memory.json
coding-agent --workspace /tmp/tp
```

Then type these at the `>` prompt, one at a time:

| # | Type | What should happen |
|---|---|---|
| 1 | `/tools` | 9 tools from the `fs` and `shell` MCP servers |
| 2 | `What files are in this project?` | **explore**: directory listing with one-line summaries |
| 3 | `Show me how tasks are created` | **explore**: explanation with `path:line` references into `routes/tasks.py` |
| 4 | `The create task endpoint accepts empty titles. Add validation to reject them.` | **edit**: read → plan → diff → ruff → tests ✅. Flags the failing pagination test as pre-existing rather than a regression |
| 5 | `Run the tests` | **run**: one failure left, `test_list_tasks_pagination`, parsed to `file:line` |
| 6 | `Fix the failing test` | **edit**: uses the run from step 5 to find `utils/helpers.py`, fixes `paginate()`, all tests pass |
| 7 | `Always add docstrings when you edit functions. Remember that.` | **remember**: stored; `/prefs` shows it as enforced |
| 8 | `GET /tasks/<id> returns 500 for unknown ids. Return 404 instead.` | **edit**: the new preference is enforced (missing docstring → self-fix) |
| 9 | `that's wrong` then `undo` | **correct**: revert + re-plan + lesson; `undo` restores the files |
| 10 | `/trace` | Span tree of the last turn; full JSONL in `.agent/traces/` |

Check the result from another terminal: `cd /tmp/tp && python -m pytest -q`.

**No API key?** Steps 2–7 run offline with the replay LLM: `coding-agent --workspace /tmp/tp --llm replay --replay-file demos/fixtures/session1.yaml`. Its canned answers match only the session 1 wording (see `demos/session1.txt`). Any other request gets `replay: no fixture response`.

**Starting over:** `rm -rf /tmp/tp /tmp/agent-memory.json`, then repeat the copy. Remove a single preference with `/forget <id>`.

**Troubleshooting**

| Symptom | Cause / fix |
|---|---|
| `route → chat fallback … anthropic call failed` | Usually the key or model. Check `echo $ANTHROPIC_API_KEY` and `llm.model` in `agent.yaml` (or `AGENT_MODEL`) |
| `ANTHROPIC_API_KEY is not set` at start-up | `export` the key in the same shell that launches `coding-agent` |
| `That looks like a command-line flag` | You typed a flag at `>`. Ctrl-D, then pass it on the shell command line |
| `coding-agent: command not found` | Activate the venv and `pip install -e .` |

## Sample interaction (from the brief)

```
> What files are in this project?          explore · 1 tool call (directory listing with summaries)
> Show me how tasks are created            explore · locate → read → explain with path:line refs
> Add validation to reject empty titles    edit · read → plan → write → ruff → tests → ✅ (flags the
                                            pre-existing pagination failure instead of "fixing" it)
> Run the tests                            run · 1 failure, parsed into file:line + assertion
> Fix the failing test                     edit · uses the failure from session memory to find
                                            utils/helpers.py, fixes paginate(), tests pass
> Always add docstrings … Remember that.   remember · stored and enforced by an AST policy check
```

## Demo recordings

| Session | Shows |
|---|---|
| `session1` | The sample interaction above: all five trajectories, memory-guided locate, baseline vs regression |
| `session2` | **New process.** The stored preference is enforced (docstring violation → self-fix), ruff auto-fix, an ambiguous request routed by the LLM, *"that's wrong"* → revert + re-plan + lesson, *undo*, and `/trace` |
| `session3` | Forgetting a preference, dry run, an edit that breaks 4 tests where the fix makes no progress → loop guard → automatic byte-exact rollback, then a successful retry with clarified intent, and `/memory` |

Play a recording with `asciinema play demos/recordings/session2.cast`, or read the `.txt`. To re-record, run `make demos`.

> **How the recordings were made.** They use the deterministic `replay` LLM: the model's text for each plan, fix or explanation is canned in `demos/fixtures/*.yaml`. **Everything else is real**: routing, MCP tool calls, memory, ruff, pytest, the loop guard and rollback. This makes them reproducible and is also how the integration tests run. To record the same scripts against a live model, run `make demos-live` (needs `ANTHROPIC_API_KEY`). Live output will differ in wording and in the exact edits.

## Tests

No API key needed. The integration tests use the replay LLM, and each test works on a temporary copy of `target_project/`.

```bash
make test                                   # 45 tests, under a minute
make lint                                   # ruff over coding_agent/ and tests/
python -m pytest tests/test_flows.py -q     # integration tests only
python -m pytest -q -k rollback             # filter by name
make demos                                  # re-record the three sessions (replay, deterministic)
```

- **Unit tests:** patcher, pytest and ruff parsers, loop guard, policies, all memory tiers (LRU, staleness, compaction, budgeted retrieval, corrupt-store recovery), and router rules and fallbacks.
- **Integration tests** (real MCP servers on a temporary copy of the target project):
  - read-before-write ordering
  - pre-existing vs regression classification
  - run → fix using session memory
  - bounded fix loop with byte-exact rollback
  - patch re-plan
  - undo, including the conflict case where the user edited the file afterwards
  - correction → lesson
  - preference persisted across agent instances and enforced
  - dry run writes nothing
  - **fault injection:** a write fails mid-edit → partial writes rolled back
  - model outage
  - the read cache avoids redundant reads

## Layout

```
coding_agent/
  agent.py, cli.py, graph.py, router.py, state.py, runtime.py, config.py, prompts.py
  trajectories/   base.py (registry + GraphBuilder), core.py, explore, edit, run, correct, remember, chat
  memory/         session.py, persistent.py, text.py (keywords / stemming / overlap)
  feedback/       parsers.py (pytest, ruff), guard.py (loop brakes), policies.py (enforceable prefs)
  edits/          journal.py (transactions, rollback), patch.py (search/replace)
  tools/          registry.py (MCP client, capabilities, validation, cache)
  mcp_servers/    filesystem.py, shell.py
  observability/  tracer.py
target_project/   the Flask task-manager API with planted defects
demos/            session scripts, replay fixtures, recordings
docs/             WRITEUP.md, RUBRIC.md, architecture.*, state_graph.* (scripts/export_graph.py)
tests/
```

## Target codebase: planted issues

1. `POST /tasks` accepts empty or whitespace titles (validation bug).
2. `GET /tasks/<id>` raises `KeyError` → 500 for unknown ids (missing error handler).
3. `utils.paginate` has an off-by-one, so `test_list_tasks_pagination` fails from the start.
4. `routes/users.py` has no tests (incomplete coverage).

## Safety notes

- The filesystem server is sandboxed to the workspace (path-escape checks).
- Writes use sha256 compare-and-swap, so the agent never overwrites changes it hasn't seen.
- The shell server runs argv only (no pipes or `&&`), checks an executable allow-list, pins the working directory, enforces a timeout, truncates output, and strips `*_KEY`/`*_TOKEN` env vars.
- This is a guardrail, not a sandbox. Run the agent in a container for untrusted code.
