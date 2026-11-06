import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from scripts.answer_output_contract import render_structured_answer, render_evidenced_answer


CONTEXTS = [{"source": "Synthetic OSCAL", "locator": "x-1", "page": None},
            {"source": "Synthetic pages", "page": 7}]


def test_renderer_uses_original_pages_and_locators_in_model_selected_order():
    result = render_structured_answer(json.dumps({"answer": "Direct answer", "source_ids": [2, 1]}), CONTEXTS)
    assert result == "Direct answer\n\n【出处】[资料2]《Synthetic pages》第7页；[资料1]《Synthetic OSCAL》定位【x-1】"


def test_original_evidence_is_materialized_by_host_not_model_translation():
    contexts = [{**CONTEXTS[0], "text": "Title\nOriginal control statement."}]
    raw = json.dumps({"answer": "中文解释。", "source_ids": [1],
                      "source_lines": [{"source_id": 1, "line": 2}]})
    result = render_evidenced_answer(raw, contexts)
    assert "中文解释（非逐字原文）" in result
    assert "> Original control statement." in result
    assert result.endswith("【出处】[资料1]《Synthetic OSCAL》定位【x-1】")


@pytest.mark.parametrize("lines", [[], [{"source_id": 1, "line": True}],
    [{"source_id": 1, "line": "2"}], [{"source_id": 1, "line": 3}],
    [{"source_id": 2, "line": 1}], [{"source_id": 1, "line": 2, "quote": "fake"}],
    [{"source_id": 1, "line": 2}, {"source_id": 1, "line": 2}]])
def test_invalid_or_model_written_original_evidence_is_rejected(lines):
    raw = json.dumps({"answer": "A", "source_ids": [1], "source_lines": lines})
    with pytest.raises(ValueError):
        render_evidenced_answer(raw, [{**CONTEXTS[0], "text": "Title\nOriginal."}])


def test_answer_request_keeps_reading_aid_out_of_canonical_lines():
    from scripts.eval_nist_batch import request_content

    context = {**CONTEXTS[0], "text": "Original line"}
    aid = {"source_ref": 1, "canonical_text_sha256": hashlib.sha256(b"Original line").hexdigest(),
           "text": "仅阅读辅助"}
    case = {"question": "Q", "reading_aids": [aid]}
    payload = json.loads(request_content("answer_structured", case, {"contexts": [context]}, "", {
        "evidence_protocol": "answer_source_lines"}))
    assert payload["question"] == "Q"
    assert payload["canonical_contexts"][0]["lines"] == [{"line": 1, "text": "Original line"}]
    assert payload["unofficial_reading_aids"][0]["text"] == "仅阅读辅助"
    aid["canonical_text_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="reading aid"):
        request_content("answer_structured", case, {"contexts": [context]}, "", {
            "evidence_protocol": "answer_source_lines"})


def test_guidance_fragments_preserve_parent_line_and_all_original_characters():
    from scripts.eval_nist_batch import request_content

    text = ("[x_gdn] U.S. Government guidance applies. Risk applies to a secondary notice. "
            "Languages depend on needs and demographics.")
    context = {**CONTEXTS[0], "text": text}
    case, row = {"question": "Q"}, {"contexts": [context]}
    manifest = {"evidence_protocol": "answer_source_lines", "guidance_segments": True}
    payload = json.loads(request_content("answer_structured", case, row, "", manifest))
    line = payload["canonical_contexts"][0]["lines"][0]
    assert line["line"] == 1
    assert len(line["segments"]) == 3
    assert line["segments"][0] == "[x_gdn] U.S. Government guidance applies."
    assert "".join(line["segments"]) == context["text"] == text
    assert "Risk applies" not in line["segments"][2]


def test_guidance_fragment_view_never_splits_statements_or_parameter_json():
    from scripts.eval_nist_batch import request_content

    text = '[x_smt] Preserve this clause. Keep its exact line.\n[{"id":"x.1","label":"A. B."}]'
    context = {**CONTEXTS[0], "text": text}
    payload = json.loads(request_content("answer_structured", {"question": "Q"}, {"contexts": [context]}, "", {
        "evidence_protocol": "answer_source_lines", "guidance_segments": True}))
    assert [line["text"] for line in payload["canonical_contexts"][0]["lines"]] == text.splitlines()


def test_native_parameter_context_binds_to_actual_referencing_action():
    from scripts.eval_nist_batch import request_content
    from scripts.inline_oscal_parameters import MARKER

    text = ("[x.a] User data in {{ insert: param, scope }}.\n[x.b] All system data." + MARKER
            + json.dumps([{"id": "scope", "label": "components"}]))
    context = {**CONTEXTS[0], "text": text}
    payload = json.loads(request_content("answer_structured", {"question": "Q"}, {"contexts": [context]}, "", {
        "evidence_protocol": "answer_source_lines", "native_parameter_bindings": True}))
    assert payload["canonical_contexts"][0]["parameter_bindings"] == [{"parameter_id": "scope",
        "referenced_in": [{"line": 1, "original_clause": "[x.a] User data in {{ insert: param, scope }}."}]}]
    assert context["text"] == text


@pytest.mark.parametrize("value", [[], {"answer": "A", "source_ids": []},
    {"answer": "", "source_ids": [1]}, {"answer": "A", "source_ids": [True]},
    {"answer": "A", "source_ids": [3]}, {"answer": "A", "source_ids": [1, 1]},
    {"answer": "A【出处】fake", "source_ids": [1]},
    {"answer": "A[资料2]", "source_ids": [1]},
    {"answer": "A", "source_ids": [1], "invented_page": 99}])
def test_invalid_model_contract_never_becomes_a_rendered_answer(value):
    with pytest.raises(ValueError):
        render_structured_answer(json.dumps(value), CONTEXTS)


@pytest.mark.parametrize("mode,bad_selection", [("answer_contract", False), ("answer_pipeline", False),
    ("answer_unit_pipeline", False), ("answer_unit_pipeline", True), ("answer_card_pipeline", False)])
def test_answer_contract_executor_keeps_both_arms_at_frozen_temperature(tmp_path, monkeypatch, mode, bad_selection):
    import dotenv
    import openai
    import tiktoken
    import truststore
    from scripts import eval_nist_batch

    packet, output = tmp_path / "packet", tmp_path / "output"
    packet.mkdir()
    context = {"source": "Synthetic", "locator": "x", "source_url": "https://example.invalid", "text": "Source"}
    digest = hashlib.sha256(b"Source").hexdigest()
    unit_sources = [{"canonical_text_sha256": digest, "source_url": context["source_url"], "locator": "x", "units": [
        {"id": digest + ":1:1", "parent_line": 1, "part": 1, "start_codepoint": 0, "end_codepoint": 6, "text": "Source"}]}]
    for name, value in {"cases.json": [{"id": "c", "question": "Q", "fixed_contexts": [context], "unit_sources": unit_sources}],
                        "chunks.json": [context], "prompts.json": {"answer_baseline_system": "Plain",
                                                                     "answer_structured_system": "JSON",
                                                                     "answer_english_system": "English",
                                                                     "evidence_select_system": "Select",
                                                                     "answer_candidate_system": "Translate"}}.items():
        (packet / name).write_text(json.dumps(value), encoding="utf-8")
    planned = 3 if mode in {"answer_pipeline", "answer_unit_pipeline"} else 2
    manifest = {"mode": mode, "answer_temperature": .3, "frozen_sha256": {},
                "paid_proposal": {"calls": planned, "budget_usd": .03}}
    (packet / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (packet / "authorization.json").write_text(json.dumps({"max_calls": planned, "budget_usd": .03,
        "manifest_sha256": hashlib.sha256((packet / "manifest.json").read_bytes()).hexdigest()}), encoding="utf-8")
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        content = ('{"unit_ids":["s1:l1:p1"],"insufficient":false}' if len(calls) == 2
                   else '{"answer":"A","unit_ids":["s1:l1:p1"]}') if mode == "answer_unit_pipeline" else (
                   "English final answer" if mode == "answer_pipeline" and len(calls) == 2
                   else '{"answer":"A","source_ids":[1]}')
        if bad_selection and len(calls) == 2:
            content = '{"unit_ids":["unknown"],"insufficient":false}'
        if mode == "answer_card_pipeline" and len(calls) == 2:
            content = '{"unit_ids":["s1:l1:p1"],"answer_type":"explain"}'
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=2, completion_tokens=2),
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
                content=content))],
            model_dump=lambda: {"model": "synthetic transport response", "usage": {"prompt_tokens": 2, "completion_tokens": 2}})

    monkeypatch.setattr(eval_nist_batch, "ROOT", tmp_path)
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *a, **kw: {})
    monkeypatch.setattr(truststore, "inject_into_ssl", lambda: None)
    monkeypatch.setattr(tiktoken, "get_encoding", lambda name: SimpleNamespace(encode=lambda text: [1]))
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(sys, "argv", ["eval", "--packet", str(packet), "--output", str(output), "--execute"])
    if bad_selection:
        with pytest.raises(ValueError, match="unavailable"):
            eval_nist_batch.main()
        assert len(calls) == 2
        summary = json.loads((output / "summary.json").read_text())
        assert summary["provider_calls"] == 2 and not summary["cost_unknown"]
        assert json.loads((output / "execution-status.json").read_text())[0]["status"] == "failed"
        return
    eval_nist_batch.main()
    assert len(calls) == planned
    expected_temperatures = ([.3, 0, .3] if mode == "answer_unit_pipeline" else
                             [.3, 0] if mode == "answer_card_pipeline" else [.3] * planned)
    assert [c["temperature"] for c in calls] == expected_temperatures
    assert "response_format" not in calls[0]
    if mode == "answer_pipeline":
        assert "English final answer" in calls[2]["messages"][1]["content"]
        assert "English final answer" not in calls[1]["messages"][1]["content"]
        assert calls[2]["response_format"] == {"type": "json_object"}
    elif mode == "answer_unit_pipeline":
        assert "selected_evidence" in json.loads(calls[2]["messages"][1]["content"])
        assert calls[1]["response_format"] == calls[2]["response_format"] == {"type": "json_object"}
    else:
        assert calls[1]["response_format"] == {"type": "json_object"}
    assert all(c["extra_body"] == {"thinking": {"type": "disabled"}} for c in calls)
    summary = json.loads((output / "summary.json").read_text())
    assert summary["provider_calls"] == planned and summary["completed"] == 1 and not summary["cost_unknown"]
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
