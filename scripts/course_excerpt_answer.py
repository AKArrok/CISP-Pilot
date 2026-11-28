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

COURSE_QUESTION_PARTS_SYSTEM = """你是 CISP 课程原文选择器。先按提问顺序识别本轮每个独立要求，
包括分别定义、说明关系和具体限制。不要拆开同一个判断中的限定词，也不要把上轮的问题当作本轮要求。
只输出 JSON：{"parts":[{"ask":"逐字复制本轮问题中该要求的连续文字","ranges":[{"source":1,"start":2,"end":4}]}]}。
每个要求都要有一个条目；ask 必须逐字按顺序出现，不能重叠。source 是资料编号，start/end 是包含首尾的原文行号。
每项只选直接回答该项的完整原文行及必要前提。仅有关联的主题、目录、邻近事项或举例不能证明所问关系。
部分有依据时按项选行，缺少直接依据的项用空 ranges。完整回答时不要臆造额外缺口。
当前证据未规定时保留缺口，不能推成允许、禁止或相反结论。历史只用于指代，不作为证据。"""

COURSE_QUALIFIED_PARTS_SYSTEM = COURSE_QUESTION_PARTS_SYSTEM + """
关系问项可由明确写出的联合主体、适用条件或共同结果提供限定依据。原文明确说明某主体与另一措施共同影响
所问结果时，选保留联合主体、前提及完整效果的原文，并在该问项内添加 "qualification":"conditional"。
这类原文是有范围的关系答案，不能扩成单独作用、互相替代、无条件保证。仅有定义并列、关键词共现或无关活动时，仍用空 ranges。
程序展示限定提示。其余问项结构保持一致，不写推导句。"""

COURSE_COMPOSED_PARTS_SYSTEM = COURSE_QUALIFIED_PARTS_SYSTEM + """
完整问答的关系或比较问项在选好依据后，还要用 explanation 给出一段清楚的关系说明（最多360字），说明双方如何关联。
只能综合该问项选中的原文，保留前提、联合主体、范围和原强度。不要把联合效果写成单方保证或把并列定义写成因果关系。
explanation 不写编号或出处标记，程序会附上相关原文及出处。没有选段的问项不能输出 explanation。
如果选中的定义在前一问已经完整呈现，仍可作为关系说明的依据，原文只展示一次。"""

COURSE_REVIEW_COVERAGE = COURSE_REVIEW_SCOPE + """
独立按题目逐项核对待审回答，不要直接沿用回答给出的分项。另输出 coverage_status（pass、fail 或 uncertain）
及 coverage_reason（点明遗漏或缺证的具体要求）。有直接原文却漏答，或缺证但未指出具体是哪一项，判 fail。
有依据的部分已回答，且其余逐项明确缺证，coverage_status 可为 pass。若仅剩无依据的判断，明确指出缺证也可通过。
scope_status 为 fail 或 coverage_status 为 fail 时，总评必须为 fail，即使已答观点都有原文。"""


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
    answer = render_course_excerpts(json.dumps({"excerpts": _course_line_excerpts(ranges, contexts)}), contexts)
    if coverage == "partial":
        answer = "以下资料能支持部分回答，其余部分缺少直接依据。\n\n" + answer
    return answer


def _course_line_excerpts(ranges: list, contexts: list[dict]) -> list[dict]:
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
    return excerpts


def render_course_question_parts(raw: str, question: str, contexts: list[dict], *, allow_qualified: bool = False,
                                 allow_explanation: bool = False) -> str:
    """Render exact question anchors and exact source lines, or a gap per part."""
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != {"parts"}
            or not isinstance(value["parts"], list) or not value["parts"]):
        raise ValueError("Expected nonempty question parts")
    rows, has_excerpt = [], False
    emitted = set()
    offset = 0
    for index, part in enumerate(value["parts"], 1):
        if not isinstance(part, dict):
            raise ValueError("Expected a question anchor and source ranges")
        qualified = "qualification" in part
        fields = {"ask", "ranges", "qualification"} if allow_qualified and qualified else {"ask", "ranges"}
        if allow_explanation and "explanation" in part:
            fields.add("explanation")
        if (set(part) != fields
                or not isinstance(part["ask"], str) or not part["ask"].strip()
                or not isinstance(part["ranges"], list)):
            raise ValueError("Expected a question anchor and source ranges")
        if qualified and (part["qualification"] != "conditional" or not part["ranges"]):
            raise ValueError("A qualified relationship needs original evidence")
        ask = part["ask"]
        position = question.find(ask, offset)
        if position < 0 or any(marker in ask for marker in ("[资料", "【出处】", "\n", "\r")):
            raise ValueError("Question anchors must be original and ordered")
        offset = position + len(ask)
        rows.append(f"{index}. 本问「{ask}」")
        excerpts = _course_line_excerpts(part["ranges"], contexts)
        if qualified:
            rows.append("课件给出了以下有条件的联系，适用范围以原文所列条件为准。")
        if "explanation" in part:
            explanation = part["explanation"]
            if (not excerpts or not isinstance(explanation, str) or not explanation.strip() or len(explanation) > 360
                    or any(marker in explanation for marker in ("[资料", "【出处】", "\n", "\r"))):
                raise ValueError("A bounded explanation needs original evidence and no citation markup")
            refs = "".join(f"[资料{source}]" for source in dict.fromkeys(item["source"] for item in excerpts))
            rows.append(f"{explanation.strip()} {refs}")
        if not excerpts:
            rows.append("现有课程资料对这一项缺少直接依据。")
        previous_quotes = set(emitted) if allow_explanation else set()
        for item in excerpts:
            source, quote = item["source"], item["quote"]
            if not quote.strip():
                raise ValueError("Empty excerpt")
            if (source, quote) in previous_quotes:
                continue
            emitted.add((source, quote))
            has_excerpt = True
            rows.append(f"{quote} [资料{source}]")
        if excerpts and all((item["source"], item["quote"]) in previous_quotes for item in excerpts) and "explanation" not in part:
            refs = "".join(f"[资料{source}]" for source in dict.fromkeys(item["source"] for item in excerpts))
            rows.append(f"相关原文见前面的引文。{refs}")
    answer = "\n\n".join(rows)
    return render_selected_citations(answer, contexts) if has_excerpt else answer


def build_course_review_message(
    question: str, evidence: str, answer: str, previous: tuple[str, str] | None = None,
) -> str:
    context = ""
    if previous is not None:
        user, assistant = previous
        context = ("\n\n同一会话上一轮（仅用于理解指代，不作为课件依据）\n"
                   f"用户 {user[:200]}\n助手 {assistant[:200]}")
    return f"本轮问题\n{question}{context}\n\n本轮课件证据\n{evidence}\n\n待审回答\n{answer}"
