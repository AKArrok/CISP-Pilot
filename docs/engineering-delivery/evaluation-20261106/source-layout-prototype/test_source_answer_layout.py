"""Source layout preserves prose and never borrows parameters across clauses."""
import asyncio
import copy
import json
from pathlib import Path

import pytest

from agents.sources import source_answer_text


MARKER = "\nOriginal OSCAL parameter definitions (no values assigned by this extraction):\n"
NOTES = "\n\n原文参数引用位置（机械提取；不扩展适用范围，不赋值）：\n"


def fixture():
    return {"locator": "toy", "page": None, "text": (
        "Synthetic source\n[toy_smt.a] Back up user data on {{ insert: param, objects }}.\n"
        "[toy_smt.b] Back up documents {{ insert: param, frequency }}.\n"
        "[toy_gdn] Consider a second notice based on risk. Consider languages based on users."
        + MARKER + json.dumps([{"id": "objects", "label": "components"},
                               {"id": "frequency", "label": "frequency"},
                               {"id": "unused", "label": "not referenced"}]))}


def test_bindings_keep_original_clauses_and_unassigned_definitions():
    source = fixture()
    before = copy.deepcopy(source)
    rendered, notes = source_answer_text(source).split(NOTES)
    bindings = json.loads(notes)
    assert {p["parameter"]["id"]: p["referenced_in"] for p in bindings} == {
        "objects": ["toy_smt.a"], "frequency": ["toy_smt.b"]}
    assert " ".join(rendered.split()) == " ".join(source["text"].split())
    assert source == before
    assert all("values" not in b["parameter"] for b in bindings)
    assert "based on risk.\nConsider languages based on users." in rendered


@pytest.mark.parametrize("change", ["plain", "page", "malformed", "duplicate", "unknown"])
def test_unrecognized_or_ambiguous_input_is_unchanged(change):
    source = fixture()
    if change == "plain":
        source["text"] = "Ordinary course prose. Another sentence."
    elif change == "page":
        source["page"] = 4
    elif change == "malformed":
        source["text"] = source["text"].split(MARKER)[0] + MARKER + "bad JSON"
    elif change == "duplicate":
        source["text"] = source["text"].split(MARKER)[0] + MARKER + '[{"id":"objects"},{"id":"objects"}]'
    else:
        source["text"] = source["text"].split(MARKER)[0] + MARKER + '[{"id":"unknown"}]'
    assert source_answer_text(source) == source["text"]


def test_real_frozen_backup_scope_is_only_referenced_by_user_information_clause():
    packet = Path(__file__).resolve().parents[1] / "evaluation/nist-public-http-default-baseline-20261106"
    cases = json.loads((packet / "cases.json").read_text(encoding="utf-8"))
    original = next(c["reference_evidence"] for c in cases if c["id"] == "cp-9-new-07")
    rendered = source_answer_text({"text": original, "page": None})
    bindings = json.loads(rendered.split(NOTES)[1])
    assert {b["parameter"]["id"]: b["referenced_in"] for b in bindings} == {
        "cp-09_odp.01": ["cp-9_smt.a"], "cp-09_odp.02": ["cp-9_smt.a"],
        "cp-09_odp.03": ["cp-9_smt.b"], "cp-09_odp.04": ["cp-9_smt.c"]}
    assert rendered[:120] == original[:120]


def test_answer_node_uses_layout_without_mutating_retrieved_source(monkeypatch):
    from langchain_core.messages import AIMessage
    from agents import graph

    context = {**fixture(), "source": "Synthetic OSCAL", "domain": "synthetic"}
    original = copy.deepcopy(context)
    captured = []

    async def invoke(llm, messages):
        captured.extend(messages)
        return AIMessage(content="Synthetic answer", usage_metadata={
            "input_tokens": 1, "output_tokens": 1, "total_tokens": 2})

    monkeypatch.setattr(graph, "get_answer_llm", lambda: object())
    monkeypatch.setattr(graph, "llm_ainvoke_retry", invoke)
    result = asyncio.run(graph.answer({"question": "Synthetic question", "contexts": [context]}))
    assert result["answer"] == "Synthetic answer"
    assert NOTES in captured[1].content
    assert '"referenced_in": ["toy_smt.a"]' in captured[1].content
    assert "用户问题: Synthetic question" in captured[1].content
    assert context == original
