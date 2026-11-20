import pytest

from coding_agent.router import route, rule_scores
from tests.conftest import rule


@pytest.mark.parametrize("text,expected", [
    ("What files are in this project?", "explore"),
    ("Show me how tasks are created", "explore"),
    ("Add validation to reject empty task titles", "edit"),
    ("The endpoint accepts empty titles. Add validation to reject them.", "edit"),
    ("Run the tests", "run"),
    ("Check linting", "run"),
    ("Fix the failing test", "edit"),
    ("Undo that", "correct"),
    ("That's wrong — use 422", "correct"),
    ("Always add docstrings when you edit functions. Remember that.", "remember"),
])
def test_rules_route_sample_interactions_without_llm(make_agent, text, expected):
    agent = make_agent()
    decision = route(text, agent.rt)
    assert decision["trajectory"] == expected and decision["source"] == "rules"
    assert agent.rt.llm.calls == []


def test_ambiguous_request_goes_to_model(make_agent):
    agent = make_agent([rule("route", '{"trajectory":"edit","confidence":0.9,"reason":"r"}')])
    text = "GET /tasks/<id> 500s for unknown ids — 404 instead"
    assert max(rule_scores(text).values(), default=0) < 1.0
    d = route(text, agent.rt)
    assert d == {"trajectory": "edit", "confidence": 0.9, "source": "model", "reason": "r"}


def test_invalid_model_answer_falls_back_to_chat(make_agent):
    agent = make_agent([rule("route", '{"trajectory":"deploy","confidence":0.9,"reason":"r"}')])
    assert route("hmm, the thing with the stuff", agent.rt)["trajectory"] == "chat"


def test_model_outage_uses_best_rule_guess(make_agent):
    agent = make_agent()   # no fixtures → every LLM call raises
    d = route("maybe swap it instead", agent.rt)
    assert d["source"] == "fallback" and d["trajectory"] == "correct"
