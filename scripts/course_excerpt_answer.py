"""Opt-in course answer renderer for source excerpts selected by a model."""

import json

from agents.sources import render_selected_citations, source_location


COURSE_EXCERPT_SYSTEM = """你是 CISP 课程原文选择器。按照问题选择能直接回答的连续完整原文行。
只输出 JSON 对象 {"excerpts":[{"source":1,"quote":"逐字原文"}]}，source 对应资料编号。
保留完整、相关的前提、范围及续行。若没有直接依据，返回空列表。
问题所问结论需要分类、层级或排他关系时，只选明确表达该关系的原文。
逐项选出完整条目及必要前提，保留同一列表的原顺序。
不要加入自写句子、总数、标题、分类或说明。"""

COURSE_LINE_SYSTEM = """你是 CISP 课程原文选择器。只输出 JSON 对象：
{"coverage":"supported","ranges":[{"source":1,"start":2,"end":4}]}。
source 是资料编号，start 和 end 是连续原文行的 L 编号，包含首尾。程序会取回原文，无需复抄。
资料只能支持部分问题时，选择有依据的完整原文行并标为 partial。拒绝补写缺少原文的分类、排他或图示关系。
完全没有相关依据才用 insufficient 和空数组。完整保留所选事项的明确前提、限制、续行和顺序。
历史只用于理解指代，不能当作原文依据。不要输出额外字段、结论或数量。"""

COURSE_FOCUSED_LINE_SYSTEM = COURSE_LINE_SYSTEM + """
先区分题目中可以从原文回答的部分和需要额外证据的部分。即使题设使用“只能”或“无关”，
只要原文直接提供所问关系中的一侧，就选择这些行并使用 partial。原文没有回应任何所问事项时才返回空数组。
追问先按历史确定指代和最具体的阶段，再只选该阶段中直接回答所问事项的行及必要前提。
同页其他阶段或同级事项即使有证据，也不要捎带选入。不要将标题或出处元数据作为关系依据。"""

COURSE_REVIEW_SCOPE = """
另在同一 JSON 对象提供 scope_status（pass、fail 或 uncertain）和 scope_reason（具体理由）。
根据本轮问题和仅供指代的上一轮，明确本轮要求回答的对象，检查是否多选了有依据但不属于本问的内容。
一旦存在多选，scope_status 必须为 fail，并在理由中点明多选的原文内容。
如果证据仅支持部分回答，检查是否呈现有依据的部分并指出缺口。不要把遗漏或用户的题设写成回答实际提出的主张。"""


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


def number_course_contexts(contexts: list[dict]) -> str:
    blocks = []
    for source, context in enumerate(contexts, 1):
        lines = "\n".join(f"L{index} {line}" for index, line in enumerate(context["text"].split("\n"), 1))
        blocks.append(f"资料{source}《{context['source']}》{source_location(context)}\n{lines}")
    return "\n\n".join(blocks)


def render_course_lines(raw: str, contexts: list[dict]) -> str:
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"coverage", "ranges"}:
        raise ValueError("Expected coverage and source line ranges")
    coverage, ranges = value["coverage"], value["ranges"]
    if (coverage not in ("supported", "partial", "insufficient") or not isinstance(ranges, list)
            or bool(ranges) != (coverage != "insufficient")):
        raise ValueError("Invalid coverage or selection")
    originals = [context["text"].split("\n") for context in contexts]
    used = set()
    excerpts = []
    for item in ranges:
        if not isinstance(item, dict) or set(item) != {"source", "start", "end"}:
            raise ValueError("Expected a source and inclusive line boundaries")
        source, start, end = item["source"], item["start"], item["end"]
        if (any(type(n) is not int for n in (source, start, end)) or not 1 <= source <= len(originals)
                or not 1 <= start <= end <= len(originals[source - 1])):
            raise ValueError("Source line range out of bounds")
        positions = {(source, line) for line in range(start, end + 1)}
        if positions & used:
            raise ValueError("Overlapping source line ranges")
        used.update(positions)
        excerpts.append({"source": source, "quote": "\n".join(originals[source - 1][start - 1:end])})
    answer = render_course_excerpts(json.dumps({"excerpts": excerpts}), contexts)
    if coverage == "partial":
        answer = "以下资料能支持部分回答，其余部分缺少直接依据。\n\n" + answer
    return answer


def build_course_review_message(
    question: str, evidence: str, answer: str, previous: tuple[str, str] | None = None,
) -> str:
    context = ""
    if previous is not None:
        user, assistant = previous
        context = ("\n\n同一会话上一轮（仅用于理解指代，不作为课件依据）\n"
                   f"用户 {user[:200]}\n助手 {assistant[:200]}")
    return f"本轮问题\n{question}{context}\n\n本轮课件证据\n{evidence}\n\n待审回答\n{answer}"
