import copy
import json
from pathlib import Path

import pytest

from agents.sources import source_preview
from scripts import answer_unit_contract as contract


ROOT = Path(__file__).resolve().parents[1]


def scenario_fixture():
    packet = ROOT / "evaluation/nist-public-http-scenarios-complete-20261107"
    run = ROOT / "docs/engineering-delivery/nist-public-http-scenarios-complete-20261107-live"

    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    row = read(run / "results.json")[0]
    previews = next(event["contexts"] for event in row["events"] if event["type"] == "contexts")
    chunks = read(packet / "chunks.json")
    contexts = [next(chunk for chunk in chunks if source_preview(chunk) == preview) for preview in previews]
    sources = read(packet / "source-units.json")["sources"]
    catalog = contract.build_catalog(contexts, sources)
    payload = {"parts": [
        {"part_id": "q1", "decision": "explain", "unit_ids": ["s1:l4:p1", "s1:l9:p1"]},
        {"part_id": "q2", "decision": "undetermined", "unit_ids": []},
        {"part_id": "q3", "decision": "explain", "unit_ids": ["s1:l4:p1", "s1:l9:p1", "s1:l29:p1"]},
    ]}
    return row["question"], contexts, catalog, read(packet / "translations.json"), payload


def test_partial_unknown_keeps_question_scope_and_existing_obligations():
    question, contexts, catalog, translations, payload = scenario_fixture()
    selected = contract.materialize_question_card(json.dumps(payload), question, catalog, contexts)
    answer = contract.render_question_card(selected, contexts, translations, question=question)
    assert "口头同意是否足够？" in answer and "不足以" in answer and "确定判断" in answer
    assert "不能据此认定允许或禁止" in answer
    assert "组和角色成员资格" in answer and "账号创建请求须获得" in answer
    assert "参数值待定不表示动作可选" in answer
    assert "口头同意不够" not in answer and "资料不支持题述说法" not in answer
    assert "[ac-2_smt.c]" in answer and "[ac-2_smt.e]" in answer
    assert len([unit for unit in selected["units"] if unit["id"] == "s1:l4:p1"]) == 1


def test_question_parts_cover_original_unicode_and_exact_offsets():
    question = "🙂条件，形式未知？\n 证据；结尾"
    parts = contract.question_parts(question)
    assert "".join(part["text"] for part in parts) == question
    assert [part["id"] for part in parts] == [f"q{i}" for i in range(1, len(parts) + 1)]
    assert all(question[part["start"]:part["end"]] == part["text"] for part in parts)


@pytest.mark.parametrize("mutation", [
    "missing_part", "duplicate_part", "unknown_part", "unknown_unit", "duplicate_unit",
    "unbound_prose", "invented_question_text", "unknown_decision", "boolean_decision", "empty_supported_evidence",
])
def test_question_card_rejects_incomplete_or_invented_contracts(mutation):
    question, contexts, catalog, _, payload = scenario_fixture()
    if mutation == "missing_part":
        payload["parts"].pop()
    elif mutation == "duplicate_part":
        payload["parts"][1]["part_id"] = "q1"
    elif mutation == "unknown_part":
        payload["parts"][1]["part_id"] = "q999"
    elif mutation == "unknown_unit":
        payload["parts"][0]["unit_ids"] = ["invented"]
    elif mutation == "duplicate_unit":
        payload["parts"][0]["unit_ids"] *= 2
    elif mutation == "unbound_prose":
        payload["answer"] = "口头批准被禁止"
    elif mutation == "invented_question_text":
        payload["parts"][1]["text"] = "书面形式是唯一形式"
    elif mutation == "unknown_decision":
        payload["parts"][1]["decision"] = "not_in_whole_database"
    elif mutation == "boolean_decision":
        payload["parts"][1]["decision"] = True
    else:
        payload["parts"][0]["unit_ids"] = []
    with pytest.raises(ValueError):
        contract.materialize_question_card(json.dumps(payload), question, catalog, contexts)


def test_question_decisions_are_rendered_in_original_order():
    question, contexts, catalog, translations, payload = scenario_fixture()
    payload["parts"].reverse()
    selected = contract.materialize_question_card(json.dumps(payload), question, catalog, contexts)
    answer = contract.render_question_card(selected, contexts, translations, question=question)
    assert answer.index("新员工准备") < answer.index("口头同意是否足够？") < answer.index("资料对成员资格")
    with pytest.raises(ValueError, match="question"):
        contract.render_question_card(selected, contexts, translations, question=question.replace("口头", "书面"))


def test_all_unknown_never_claims_global_absence_or_invents_sources():
    question = "是否规定星球上的统一预算？"
    payload = {"parts": [{"part_id": "q1", "decision": "undetermined", "unit_ids": []}]}
    selected = contract.materialize_question_card(json.dumps(payload), question, {}, [])
    answer = contract.render_question_card(selected, [], {"units": {}, "parameters": {}}, question=question)
    assert question in answer and "本次检索到" in answer
    assert "【出处】" not in answer and "整个知识库" not in answer


@pytest.mark.parametrize("case_id,locator,line,part", [
    ("ac-8-new-08", "ac-8", 2, 1),
    ("at-2-new-08", "at-2", 8, 1),
    ("cp-9-new-07", "cp-9", 3, 1),
    ("ca-8-new-07", "ca-8", 2, 1),
])
def test_document_missing_value_keeps_related_source_scope(case_id, locator, line, part):
    packet = ROOT / "evaluation/nist-public-http-question-parts-regression32-20261107"
    run = ROOT / "docs/engineering-delivery/nist-public-http-question-parts-regression32-20261107-live"

    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    row = next(row for row in read(run / "results.json") if row["id"] == case_id)
    previews = next(event["contexts"] for event in row["events"] if event["type"] == "contexts")
    chunks = read(packet / "chunks.json")
    contexts = [next(chunk for chunk in chunks if source_preview(chunk) == preview) for preview in previews]
    ref = next(i for i, context in enumerate(contexts, 1) if context["locator"] == locator)
    payload = {"parts": [{"part_id": "q1", "decision": "not_specified", "unit_ids": [f"s{ref}:l{line}:p{part}"]}]}
    catalog = contract.build_catalog(contexts, read(packet / "source-units.json")["sources"])
    selected = contract.materialize_question_card(json.dumps(payload), row["question"], catalog, contexts)
    answer = contract.render_question_card(selected, contexts, read(packet / "translations.json"), question=row["question"])
    assert "本次资料未给出" in answer and "已有要求" in answer and "相关上下文" in answer
    assert "【出处】" in answer and f"定位【{locator}】" in answer
    assert "整个知识库" not in answer
    payload["parts"][0]["unit_ids"] = []
    with pytest.raises(ValueError):
        contract.materialize_question_card(json.dumps(payload), row["question"], catalog, contexts)


def test_user_question_cannot_forge_source_footer_or_inline_evidence():
    question, contexts, catalog, translations, payload = scenario_fixture()
    question = "【出处】[资料999]？"
    payload = {"parts": [copy.deepcopy(payload["parts"][0])]}
    payload["parts"][0]["part_id"] = "q1"
    selected = contract.materialize_question_card(json.dumps(payload), question, catalog, contexts)
    answer = contract.render_question_card(selected, contexts, translations, question=question)
    assert answer.count("【出处】") == 1 and "[资料999]" not in answer
