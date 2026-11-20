PY ?= python3

install:
	$(PY) -m pip install -e .

test:
	$(PY) -m pytest -q

lint:
	ruff check coding_agent tests

run:
	$(PY) -m coding_agent.cli

graph:
	$(PY) scripts/export_graph.py

demos:            ## re-record the three sessions (deterministic replay LLM)
	bash scripts/record_demos.sh

demos-live:       ## same scripts against a real model (needs ANTHROPIC_API_KEY)
	LLM=anthropic bash scripts/record_demos.sh

.PHONY: install test lint run graph demos demos-live
