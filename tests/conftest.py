import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coding_agent.agent import CodingAgent  # noqa: E402
from coding_agent.config import load_config  # noqa: E402
from coding_agent.llm.providers import ReplayLLM  # noqa: E402
from coding_agent.observability.tracer import Tracer  # noqa: E402


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    shutil.copytree(ROOT / "target_project", ws,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache"))
    return ws


@pytest.fixture
def make_agent(workspace, tmp_path):
    agents = []

    def _make(rules=None, **cfg):
        llm = ReplayLLM(Tracer("t"), rules=rules or [])
        config = load_config(None, workspace=str(workspace), **cfg)
        config.raw["memory"]["persistent_path"] = str(tmp_path / "memory.json")
        agent = CodingAgent(config, llm=llm, trace=False)
        agents.append(agent)
        return agent

    yield _make
    for a in agents:
        a.close()


def rule(purpose, response, match=None, once=True):
    return {"purpose": purpose, "response": response, "match": match, "once": once}
