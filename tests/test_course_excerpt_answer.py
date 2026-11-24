import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from scripts.course_excerpt_answer import render_course_excerpts


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
