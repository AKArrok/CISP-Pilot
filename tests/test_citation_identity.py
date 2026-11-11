"""Citation identity cannot be inferred from retrieved material alone."""
import asyncio

import pytest
from langchain_core.messages import AIMessage

from agents import graph
from agents.sources import CITATION_WARNING, citation_is_verifiable, render_selected_citations


CONTEXT = [{"source": "课程资料", "page": 5, "domain": "示例", "text": "课程片段"}]


@pytest.mark.parametrize("answer,expected", [
    ("依据第5页，结论如此。", False),
    ("【出处】[资料1]《课程资料》第5页", True),
    ("[资料1]支持此点。\n【出处】[资料1]《课程资料》第5页", True),
    ("【出处】[资料1]《其他资料》第5页", False),
    ("【出处】[资料1]《课程资料》第6页", False),
    ("【出处】[资料2]《课程资料》第5页", False),
    ("[资料1]支持此点。", False),
    ("[资料2]正文。【出处】[资料1]《课程资料》第5页", False),
])
def test_citation_identity_does_not_guess(answer, expected):
    assert citation_is_verifiable(answer, CONTEXT) is expected


def test_unverified_answer_is_labeled_not_rewritten(monkeypatch):
    async def invoke(*_):
        return AIMessage(content="课程片段支持该结论。")

    monkeypatch.setattr(graph, "get_answer_llm", lambda: object())
    monkeypatch.setattr(graph, "llm_ainvoke_retry", invoke)
    result = asyncio.run(graph.answer({"question": "问题", "contexts": CONTEXT}))
    assert result["answer"] == "课程片段支持该结论。\n\n" + CITATION_WARNING
    assert result["messages"][0].content == result["answer"]


def test_render_only_explicit_valid_reference():
    answer = "依据[资料1]，可以确认。"
    result = render_selected_citations(answer, CONTEXT)
    assert result == answer + "\n\n【出处】[资料1]《课程资料》第5页"
    assert citation_is_verifiable(result, CONTEXT)
    for raw in ("没有资料编号。", "依据[资料2]。", "【出处】[资料1]《错误来源》第5页"):
        assert render_selected_citations(raw, CONTEXT) == raw
