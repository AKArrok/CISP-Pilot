import pytest
from langchain_core.messages import HumanMessage

import config
from agents.budget import RequestBudget, current_budget
from llms import get_answer_llm, get_simple_llm, reset_llms


@pytest.mark.parametrize("factory", [get_answer_llm, get_simple_llm])
@pytest.mark.parametrize("bounded", [False, True])
def test_official_deepseek_always_uses_flash_without_thinking(factory, bounded, monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "synthetic")
    reset_llms()
    token = current_budget.set(RequestBudget()) if bounded else None
    try:
        model = factory(base_url="https://api.deepseek.com/v1", model="deepseek-v4-pro",
                        extra_body={"thinking": {"type": "enabled"}}, max_tokens=80)
        payload = model._get_request_payload([HumanMessage(content="offline contract")])
        assert payload["model"] == "deepseek-flash"
        assert payload["extra_body"]["thinking"] == {"type": "disabled"}
        assert payload["extra_body"]["max_tokens"] == 80
    finally:
        if token is not None:
            current_budget.reset(token)
        reset_llms()


def test_other_endpoint_keeps_explicit_model(monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "synthetic")
    reset_llms()
    try:
        model = get_simple_llm(base_url="https://fixture.invalid/v1", model="custom-model")
        assert model.model_name == "custom-model"
    finally:
        reset_llms()


def test_evidence_model_has_the_same_official_deepseek_policy(monkeypatch):
    from agents.evidence import make_model
    monkeypatch.setattr(config, "LLM_API_KEY", "synthetic")
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setattr(config, "SIMPLE_LLM_MODEL", "deepseek-v4-pro")
    payload = make_model()._get_request_payload([HumanMessage(content="offline")])
    assert payload["model"] == "deepseek-flash"
    assert payload["extra_body"]["thinking"] == {"type": "disabled"}
