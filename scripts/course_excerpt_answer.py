"""Opt-in course answer renderer for source excerpts selected by a model."""

import json

from agents.sources import render_selected_citations


COURSE_EXCERPT_SYSTEM = """你是 CISP 课程原文选择器。按照问题选择能直接回答的连续完整原文行。
只输出 JSON 对象 {"excerpts":[{"source":1,"quote":"逐字原文"}]}，source 对应资料编号。
保留完整、相关的前提、范围及续行。若没有直接依据，返回空列表。
问题所问结论需要分类、层级或排他关系时，只选明确表达该关系的原文。
逐项选出完整条目及必要前提，保留同一列表的原顺序。
不要加入自写句子、总数、标题、分类或说明。"""


def render_course_excerpts(raw: str, contexts: list[dict]) -> str:
    """Render only complete original lines. Selection relevance still needs review."""
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != {"excerpts"}
            or not isinstance(value["excerpts"], list)):
        raise ValueError("Expected only a list of source excerpts")
    if not value["excerpts"]:
        return "本次检索到的资料不足以给出有依据的回答。"

    seen = set()
    rows = []
    for excerpt in value["excerpts"]:
        if not isinstance(excerpt, dict) or set(excerpt) != {"source", "quote"}:
            raise ValueError("Expected source and verbatim quote")
        source, quote = excerpt["source"], excerpt["quote"]
        if type(source) is not int or not 1 <= source <= len(contexts):
            raise ValueError("Unknown source")
        if not isinstance(quote, str) or not quote.strip() or (source, quote) in seen:
            raise ValueError("Empty or repeated excerpt")
        text = contexts[source - 1]["text"]
        if f"\n{quote}\n" not in f"\n{text}\n":
            raise ValueError("Quote must match complete original lines")
        seen.add((source, quote))
        rows.append(f"{len(rows) + 1}. {quote} [资料{source}]")
    return render_selected_citations("资料摘录（按片段编号）\n\n" + "\n".join(rows), contexts)
