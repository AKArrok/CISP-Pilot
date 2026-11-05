import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from scripts.answer_output_contract import render_structured_answer


CONTEXTS = [{"source": "Synthetic OSCAL", "locator": "x-1", "page": None},
            {"source": "Synthetic pages", "page": 7}]


def test_renderer_uses_original_pages_and_locators_in_model_selected_order():
    result = render_structured_answer(json.dumps({"answer": "Direct answer", "source_ids": [2, 1]}), CONTEXTS)
    assert result == "Direct answer\n\n【出处】[资料2]《Synthetic pages》第7页；[资料1]《Synthetic OSCAL》定位【x-1】"


@pytest.mark.parametrize("value", [[], {"answer": "A", "source_ids": []},
    {"answer": "", "source_ids": [1]}, {"answer": "A", "source_ids": [True]},
    {"answer": "A", "source_ids": [3]}, {"answer": "A", "source_ids": [1, 1]},
    {"answer": "A【出处】fake", "source_ids": [1]},
    {"answer": "A[资料2]", "source_ids": [1]},
    {"answer": "A", "source_ids": [1], "invented_page": 99}])
def test_invalid_model_contract_never_becomes_a_rendered_answer(value):
    with pytest.raises(ValueError):
        render_structured_answer(json.dumps(value), CONTEXTS)


def test_answer_contract_executor_keeps_both_arms_at_frozen_temperature(tmp_path, monkeypatch):
    import dotenv
    import openai
    import tiktoken
    import truststore
    from scripts import eval_nist_batch

    packet, output = tmp_path / "packet", tmp_path / "output"
    packet.mkdir()
    context = {"source": "Synthetic", "locator": "x", "source_url": "https://example.invalid", "text": "Source"}
    for name, value in {"cases.json": [{"id": "c", "question": "Q", "fixed_contexts": [context]}],
                        "chunks.json": [context], "prompts.json": {"answer_baseline_system": "Plain",
                                                                     "answer_structured_system": "JSON"}}.items():
        (packet / name).write_text(json.dumps(value), encoding="utf-8")
    manifest = {"mode": "answer_contract", "answer_temperature": .3, "frozen_sha256": {},
                "paid_proposal": {"calls": 2, "budget_usd": .03}}
    (packet / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (packet / "authorization.json").write_text(json.dumps({"max_calls": 2, "budget_usd": .03,
        "manifest_sha256": hashlib.sha256((packet / "manifest.json").read_bytes()).hexdigest()}), encoding="utf-8")
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=2, completion_tokens=2),
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"answer":"A","source_ids":[1]}'))],
            model_dump=lambda: {"model": "synthetic transport response", "usage": {"prompt_tokens": 2, "completion_tokens": 2}})

    monkeypatch.setattr(eval_nist_batch, "ROOT", tmp_path)
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *a, **kw: {})
    monkeypatch.setattr(truststore, "inject_into_ssl", lambda: None)
    monkeypatch.setattr(tiktoken, "get_encoding", lambda name: SimpleNamespace(encode=lambda text: [1]))
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(sys, "argv", ["eval", "--packet", str(packet), "--output", str(output), "--execute"])
    eval_nist_batch.main()
    assert len(calls) == 2 and all(c["temperature"] == .3 for c in calls)
    assert "response_format" not in calls[0] and calls[1]["response_format"] == {"type": "json_object"}
    assert all(c["extra_body"] == {"thinking": {"type": "disabled"}} for c in calls)
    summary = json.loads((output / "summary.json").read_text())
    assert summary["provider_calls"] == 2 and summary["completed"] == 1 and not summary["cost_unknown"]
    assert "pending_call" not in summary


@pytest.mark.parametrize("temperature", [True, -.1, "0.3"])
def test_answer_contract_rejects_invalid_temperature_before_output(tmp_path, monkeypatch, temperature):
    from scripts import eval_nist_batch

    packet, output = tmp_path / "packet", tmp_path / "output"
    packet.mkdir()
    (packet / "manifest.json").write_text(json.dumps({"mode": "answer_contract", "answer_temperature": temperature}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["eval", "--packet", str(packet), "--output", str(output), "--execute"])
    with pytest.raises(ValueError, match="temperature"):
        eval_nist_batch.main()
    assert not output.exists()
