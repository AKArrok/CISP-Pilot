import copy
import json

import pytest

from scripts import answer_unit_contract as contract
from test_question_card_contract import scenario_fixture


def grouped_fixture():
    question, contexts, catalog, translations, _ = scenario_fixture()
    payload = {"parts": [
        {"part_ids": ["q1", "q2"], "decision": "undetermined", "unit_ids": []},
        {"part_ids": ["q3"], "decision": "explain", "unit_ids": ["s1:l4:p1", "s1:l9:p1", "s1:l29:p1"]},
    ]}
    return question, contexts, catalog, translations, payload


def test_adjacent_context_stays_attached_to_the_actual_unknown_question():
    question, contexts, catalog, translations, payload = grouped_fixture()
    selected = contract.materialize_grouped_question_card(json.dumps(payload), question, catalog, contexts)
    answer = contract.render_question_card(selected, contexts, translations, question=question)
    assert "新员工准备加入组和角色并创建账号，口头同意是否足够？" in answer
    assert "不能据此认定允许或禁止" in answer and "账号创建请求须获得" in answer
    assert "资料支持这一说法" not in answer


def test_user_only_initial_training_background_is_not_claimed_as_source_support():
    _, contexts, catalog, translations, _ = grouped_fixture()
    question = "方案只做一次培训，没有后续培训，是否完整？"
    payload = {"parts": [
        {"part_ids": ["q1", "q2"], "decision": "context", "unit_ids": []},
        {"part_ids": ["q3"], "decision": "explain", "unit_ids": ["s1:l4:p1"]},
    ]}
    selected = contract.materialize_grouped_question_card(json.dumps(payload), question, catalog, contexts)
    answer = contract.render_question_card(selected, contexts, translations, question=question)
    assert "原问题给定的背景" in answer and "未作为资料结论验证" in answer
    assert "资料支持这一说法" not in answer


@pytest.mark.parametrize("mutation", ["nonadjacent", "reverse_within_scope", "duplicate", "missing", "fake_part", "free_text", "context_citation"])
def test_grouped_scope_rejects_invented_reordered_or_uncovered_question_parts(mutation):
    question, contexts, catalog, _, payload = grouped_fixture()
    if mutation == "nonadjacent":
        payload["parts"][0]["part_ids"] = ["q1", "q3"]
    elif mutation == "reverse_within_scope":
        payload["parts"][0]["part_ids"].reverse()
    elif mutation == "duplicate":
        payload["parts"][1]["part_ids"] = ["q2", "q3"]
    elif mutation == "missing":
        payload["parts"].pop()
    elif mutation == "fake_part":
        payload["parts"][0]["part_ids"] = ["oral_is_forbidden"]
    elif mutation == "free_text":
        payload["parts"][0]["answer"] = "资料肯定只需一次培训"
    else:
        payload["parts"][1]["decision"] = "context"
    with pytest.raises(ValueError):
        contract.materialize_grouped_question_card(json.dumps(payload), question, catalog, contexts)


def test_group_order_is_reconstructed_from_original_question_not_model_order():
    question, contexts, catalog, translations, payload = grouped_fixture()
    payload["parts"].reverse()
    selected = contract.materialize_grouped_question_card(json.dumps(payload), question, catalog, contexts)
    answer = contract.render_question_card(selected, contexts, translations, question=question)
    assert answer.index("口头同意是否足够？") < answer.index("资料对成员资格")
    with pytest.raises(ValueError, match="question"):
        contract.render_question_card(selected, contexts, translations, question=question+"额外内容")


def test_context_only_is_not_presented_as_retrieved_fact_or_supported_answer():
    question, _, _, _, _ = grouped_fixture()
    payload = {"parts": [{"part_ids": ["q1", "q2", "q3"], "decision": "context", "unit_ids": []}]}
    selected = contract.materialize_grouped_question_card(json.dumps(payload), question, {}, [])
    answer = contract.render_question_card(selected, [], {"units": {}, "parameters": {}}, question=question)
    assert "未作为资料结论验证" in answer and "【出处】" not in answer
    payload["parts"][0]["decision"] = "not_specified"
    with pytest.raises(ValueError):
        contract.materialize_grouped_question_card(json.dumps(payload), question, {}, [])


def test_group_materialization_does_not_mutate_original_question_metadata():
    question, contexts, catalog, _, payload = grouped_fixture()
    before = copy.deepcopy(payload)
    contract.materialize_grouped_question_card(json.dumps(payload), question, catalog, contexts)
    assert payload == before
