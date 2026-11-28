import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from scripts.course_excerpt_answer import (
    build_course_review_message,
    number_course_contexts,
    render_course_excerpts,
    render_course_lines,
    render_course_question_parts,
)


def render(contexts, excerpts):
    return render_course_excerpts(json.dumps({"excerpts": excerpts}, ensure_ascii=False), contexts)


def test_list_numbering_comes_from_selected_items():
    contexts = [{"source": "合成课件", "page": 1, "text": "安排演练\n检查记录\n归档结果"}]
    answer = render(contexts, [{"source": 1, "quote": item} for item in contexts[0]["text"].splitlines()])
    assert [line for line in answer.splitlines() if line[:1].isdigit()] == [
        "1. 安排演练 [资料1]", "2. 检查记录 [资料1]", "3. 归档结果 [资料1]",
    ]
    assert "【出处】[资料1]《合成课件》第1页" in answer


@pytest.mark.parametrize("selection", [
    {"excerpts": [{"source": 1, "quote": "检查记录属于强制审计。"}]},
    {"excerpts": [{"source": 1, "quote": "检查记录"}], "count": 6},
    {"excerpts": [{"source": 1, "quote": "检查记录", "classification": "强制审计"}]},
    {"excerpts": [{"source": 2, "quote": "检查记录"}]},
    {"excerpts": [{"source": True, "quote": "检查记录"}]},
    {"excerpts": [{"source": 1, "quote": "检查记录"}] * 2},
])
def test_rejects_generated_claim_count_or_invalid_selection(selection):
    with pytest.raises(ValueError):
        render_course_excerpts(json.dumps(selection), [{"source": "合成课件", "page": 1, "text": "检查记录"}])


def test_explicit_relationship_and_condition_are_visible_verbatim():
    text = "仅当设备停机时，才进入维护流程。\n维护工作分为检查与更换两类。"
    answer = render([{"source": "合成课件", "page": 2, "text": text}], [{"source": 1, "quote": text}])
    assert "仅当设备停机时，才进入维护流程。" in answer
    assert "维护工作分为检查与更换两类。" in answer


def test_empty_selection_reports_insufficient_evidence():
    assert "资料不足" in render([], [])


def test_line_selection_preserves_bullets_and_wrapped_conditions():
    text = "标题\n\uf076仅当设备停机时，\n才检查设备。"
    contexts = [{"source": "合成课件", "page": 2, "text": text}]
    raw = '{"coverage":"partial","ranges":[{"source":1,"start":2,"end":3}]}'
    output = render_course_lines(raw, contexts)
    assert "\uf076仅当设备停机时，\n才检查设备。" in output
    assert "其余部分缺少直接依据" in output
    assert "L2 \uf076仅当设备停机时，\nL3 才检查设备。" in number_course_contexts(contexts)


@pytest.mark.parametrize("selection", [
    {"coverage": "partial", "ranges": []},
    {"coverage": "insufficient", "ranges": [{"source": 1, "start": 1, "end": 1}]},
    {"coverage": "supported", "ranges": [{"source": 1, "start": 2, "end": 1}]},
    {"coverage": "supported", "ranges": [{"source": 1, "start": 1, "end": 4}]},
    {"coverage": "supported", "ranges": [{"source": 1, "start": True, "end": 1}]},
    {"coverage": "supported", "ranges": [{"source": 2, "start": 1, "end": 1}]},
    {"coverage": "supported", "ranges": [
        {"source": 1, "start": 1, "end": 2}, {"source": 1, "start": 2, "end": 3},
    ]},
    {"coverage": "supported", "ranges": [{"source": 1, "start": 1, "end": 1}], "count": 6},
])
def test_line_selection_rejects_invalid_ranges_and_extra_claims(selection):
    with pytest.raises(ValueError):
        render_course_lines(json.dumps(selection), [{"source": "合成课件", "page": 1, "text": "甲\n乙\n丙"}])


def test_review_history_is_bounded_and_not_part_of_source_evidence():
    user = "上一问" * 100
    answer = "上一答" * 100
    result = build_course_review_message("那它呢？", "独立原文", "当前答复", (user, answer))
    assert user[:200] in result and user not in result
    assert answer[:200] in result and answer not in result
    assert "仅用于理解指代，不作为课件依据" in result
    assert result.endswith("本轮课件证据\n独立原文\n\n待审回答\n当前答复")
    assert "上一轮" not in build_course_review_message("当前问题", "原文", "当前答复")


def test_question_parts_identify_the_specific_gap_and_keep_source_conditions():
    contexts = [{"source": "合成规程", "page": 2, "text": "设备停机后，\n立即登记型号。"}]
    parts = [{"ask": "何时登记型号", "ranges": [{"source": 1, "start": 1, "end": 2}]},
             {"ask": "主管何时签字", "ranges": []}]
    answer = render_course_question_parts(json.dumps({"parts": parts}), "何时登记型号？主管何时签字？", contexts)
    assert "设备停机后，\n立即登记型号。 [资料1]" in answer
    assert "2. 本问「主管何时签字」\n\n现有课程资料对这一项缺少直接依据。" in answer
    assert answer.endswith("【出处】[资料1]《合成规程》第2页")


@pytest.mark.parametrize("anchors", [("虚构结论",), ("乙", "甲"), ("甲乙", "乙"), ("[资料1]",)])
def test_question_parts_reject_invented_overlapping_or_citation_anchors(anchors):
    parts = [{"ask": anchor, "ranges": []} for anchor in anchors]
    with pytest.raises(ValueError):
        render_course_question_parts(json.dumps({"parts": parts}), "甲乙？[资料1]", [])


def test_qualified_question_part_preserves_joint_subject_and_condition():
    text = "设备停机后，监测与人工巡查共同\n降低漏报。"
    contexts = [{"source": "合成手册", "page": 2, "text": text}]
    part = {"ask": "两者有什么关系", "qualification": "conditional",
            "ranges": [{"source": 1, "start": 1, "end": 2}]}
    raw = json.dumps({"parts": [part]})
    with pytest.raises(ValueError):
        render_course_question_parts(raw, "两者有什么关系？", contexts)
    answer = render_course_question_parts(raw, "两者有什么关系？", contexts, allow_qualified=True)
    assert "以下有条件的联系" in answer
    assert f"{text} [资料1]" in answer
    with pytest.raises(ValueError):
        render_course_question_parts(json.dumps({"parts": [part | {"ranges": []}]}),
                                     "两者有什么关系？", contexts, allow_qualified=True)


def test_opt_in_renderer_works_through_graph_answer(monkeypatch):
    from agents import graph
    from scripts.course_excerpt_answer import COURSE_EXCERPT_SYSTEM

    raw = json.dumps({"excerpts": [{"source": 1, "quote": "停机后检查设备。"}]}, ensure_ascii=False)
    invoke = AsyncMock(return_value=AIMessage(content=raw))
    monkeypatch.setattr(graph, "get_answer_llm", lambda: object())
    monkeypatch.setattr(graph, "llm_ainvoke_retry", invoke)
    monkeypatch.setattr(graph, "_below_relevance_floor", lambda _: False)
    monkeypatch.setattr(graph, "render_selected_citations", render_course_excerpts)
    monkeypatch.setattr(graph.prompts, "ANSWER_SYSTEM", COURSE_EXCERPT_SYSTEM)

    row = {"source": "合成课件", "page": 1, "domain": "合成", "text": "停机后检查设备。"}
    result = asyncio.run(graph.answer({"question": "何时检查设备？", "contexts": [row]}))
    assert result["answer"] == result["messages"][0].content
    assert "1. 停机后检查设备。 [资料1]" in result["answer"]
    assert "【出处】[资料1]《合成课件》第1页" in result["answer"]
    assert invoke.await_args.args[1][0].content == COURSE_EXCERPT_SYSTEM
