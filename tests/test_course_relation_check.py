import json

import pytest

from scripts.course_relation_check import (
    build_course_relation_check_message,
    course_relation_questions,
    render_course_answer_with_relation_check,
)


QUESTION = "设备监测是什么？监测与减少漏报有什么关系？"
ASK = "监测与减少漏报有什么关系？"
CONTEXTS = [
    {"source": "合成手册", "page": 1, "text": "监测记录设备状态。"},
    {"source": "合成手册", "page": 2, "text": "仅当设备完成隔离，监测与人工巡查共同\n降低漏报。"},
]


def packet(*parts):
    return json.dumps({"parts": list(parts)}, ensure_ascii=False)


def test_relation_check_finds_omitted_question_and_uses_all_evidence():
    selected = packet({"ask": "设备监测是什么", "ranges": [{"source": 1, "start": 1, "end": 1}]})
    message = build_course_relation_check_message(QUESTION, selected, CONTEXTS)
    assert f'["{ASK}"]' in message
    assert "共同\nL2 降低漏报。" in message
    checked = packet({"ask": ASK, "status": "conditional", "ranges": [{"source": 2, "start": 1, "end": 2}],
                      "explanation": "仅在设备隔离后，监测和巡查共同降低漏报。"})
    output = render_course_answer_with_relation_check(selected, checked, QUESTION, CONTEXTS)
    assert "2. 本问「监测与减少漏报有什么关系？」" in output
    assert "仅当设备完成隔离，监测与人工巡查共同\n降低漏报。 [资料2]" in output
    assert "仅在设备隔离后，监测和巡查共同降低漏报。 [资料2]" in output


def test_insufficient_check_replaces_wrong_ranges_without_losing_later_question():
    later = "需登记什么？"
    selected = packet({"ask": "设备监测是什么", "ranges": []}, {"ask": ASK, "ranges": [{"source": 1, "start": 1, "end": 1}]},
                      {"ask": later, "ranges": []})
    checked = packet({"ask": ASK, "status": "insufficient", "ranges": []})
    output = render_course_answer_with_relation_check(selected, checked, QUESTION + later, CONTEXTS)
    assert f"2. 本问「{ASK}」\n\n现有课程资料对这一项缺少直接依据。" in output
    assert f"3. 本问「{later}」" in output
    assert "[资料1]" not in output


def test_non_relation_preserves_the_ordinary_path():
    question = "安全检查的准备工作有哪些？"
    assert course_relation_questions(question) == []
    selected = packet({"ask": question, "ranges": []})
    assert "缺少直接依据" in render_course_answer_with_relation_check(selected, None, question, CONTEXTS)


@pytest.mark.parametrize("check", [
    None, packet(), packet({"ask": "补写推断", "status": "insufficient", "ranges": []}),
    packet({"ask": ASK, "status": "conditional", "ranges": [], "explanation": "虚构"}),
    packet({"ask": ASK, "status": "insufficient", "ranges": [], "explanation": "虚构"}),
])
def test_relation_coverage_is_fail_closed(check):
    selection = packet({"ask": "设备监测是什么", "ranges": []})
    with pytest.raises(ValueError):
        render_course_answer_with_relation_check(selection, check, QUESTION, CONTEXTS)


def test_bound_relation_rejects_definitions_and_partial_condition():
    selection = packet({"ask": "设备监测是什么", "ranges": [{"source": 1, "start": 1, "end": 1}]})
    contract = {ASK: {"status": "conditional", "proof": {"source": 2, "start": 1, "end": 2},
                      "facts": [{"source": 1, "start": 1, "end": 1}], "terms": ["监测", "巡查"]}}
    for source_range, explanation in (({"source": 1, "start": 1, "end": 1}, "监测必须与巡查一起。"),
                                        ({"source": 2, "start": 2, "end": 2}, "监测与巡查共同降低漏报。"),
                                        ({"source": 2, "start": 1, "end": 2}, "单靠监测可减少漏报。")):
        check = packet({"ask": ASK, "status": "conditional", "ranges": [source_range], "explanation": explanation})
        output = render_course_answer_with_relation_check(selection, check, QUESTION, CONTEXTS, evidence=contract)
        assert "本次选段未能提供可核实的关系依据。" in output
        assert explanation not in output
    check = packet({"ask": ASK, "status": "conditional", "ranges": [{"source": 2, "start": 1, "end": 2}],
                    "explanation": "在设备隔离后，监测与巡查共同降低漏报。"})
    output = render_course_answer_with_relation_check(selection, check, QUESTION, CONTEXTS, evidence=contract)
    assert "仅当设备完成隔离，监测与人工巡查共同\n降低漏报。 [资料2]" in output
    assert "可核实的关系依据" not in output


def test_bound_relation_places_known_facts_before_the_specific_gap():
    question = "材料登记和外观检查有什么关系？"
    contexts = [{"source": "合成规程", "page": 1, "text": "登记记录领用数量。\n外观检查记录划痕。"}]
    check = packet({"ask": question, "status": "insufficient", "ranges": []})
    evidence = {question: {"status": "insufficient", "proof": None, "facts": [{"source": 1, "start": 1, "end": 2}]}}
    answer = render_course_answer_with_relation_check(packet({"ask": question, "ranges": []}), check, question, contexts,
                                                      evidence=evidence)
    assert "登记记录领用数量。\n外观检查记录划痕。 [资料1]" in answer
    assert answer.index("外观检查记录划痕。") < answer.index("现有课件未直接说明本问所求的关系。")
