"""Opt-in second pass for explicit relationship questions with course evidence."""

import json
import re

from scripts.course_excerpt_answer import number_course_contexts, render_course_question_parts


COURSE_RELATION_CHECK_SYSTEM = """你只核对已列出的关系问项。逐条检查本轮全部编号课件后，再给每项选择充分证据。
只输出 JSON：{"parts":[{"ask":"逐字复制待核对的完整问项","status":"conditional","ranges":[{"source":1,"start":2,"end":4}],"explanation":"按证据说明两者关系"}]}。
status 只用 direct、conditional 或 insufficient。先找直接关系的证据，也检查明确写出的共同主体、共同结果、适用情境。
原文只在特定情境或多个主体共同作用时支持关系，则用 conditional，选择包含前提、全部共同主体及完整结果的原文，说明中也要保留这些限定。
原文若只是并列定义、关键词共现，不能据此推出关系。若没有证据，status 用 insufficient，ranges 为空，并省略 explanation。
每个有据问项提供不超过360字的 explanation，禁止自写出处，禁止把共同作用改成单方效果或保证。其他问项不用输出。
同组上一轮只用于理解指代。已有选段仅供定位遗漏，不能缩小你检查课件的范围。"""

COURSE_BOUND_RELATION_CHECK_SYSTEM = COURSE_RELATION_CHECK_SYSTEM + """
本批附有逐问项核对过的证据窗口。direct 或 conditional 的 ranges 必须完整覆盖相应关系证据窗口，不能用同页其他段落或定义代替。
关系证据窗口为空时用 insufficient；程序会先展示已核对的事实，再说明关系缺口。即使关系证据存在，也不能从定义单独推出保证。
解释所用关系的范围必须与证据窗口一致，联合主体和适用条件均照原文保留。"""

_RELATION = re.compile(r"(?:关系|联系|区别|差异|有何不同|有什么不同)(?:是|在于|呢|[？?！!。]|$)|(?:什么|哪些|何种)(?:关系|联系|区别|差异)|(?:如何|怎样)(?:区分|比较)")
_CLAUSE = re.compile(r"[^。；;？?！!\r\n]+[。；;？?！!]?")


def course_relation_questions(question: str) -> list[str]:
    """Find explicit relationship or comparison clauses in the original question."""
    return [clause for match in _CLAUSE.finditer(question)
            if (clause := match.group().strip()) and _RELATION.search(clause)]


def build_course_relation_check_message(
    question: str, selection: str, contexts: list[dict], previous: tuple[str, str] | None = None,
) -> str:
    asks = course_relation_questions(question)
    if not asks:
        raise ValueError("No explicit relationship question")
    history = "" if previous is None else f"\n\n同组上一轮（只供指代）\n用户 {previous[0][:200]}\n助手 {previous[1][:200]}"
    return (f"本轮完整问题\n{question}{history}\n\n待核对问项（按次序）\n"
            f"{json.dumps(asks, ensure_ascii=False)}\n\n已有选段\n{selection}"
            f"\n\n本轮全部编号课件\n{number_course_contexts(contexts)}")


def render_course_answer_with_relation_check(
    selection: str, check: str | None, question: str, contexts: list[dict],
    *, evidence: dict | None = None,
) -> str:
    """Replace relationship parts with separately checked line ranges and their limits."""
    asks = course_relation_questions(question)
    baseline = render_course_question_parts(selection, question, contexts)
    if not asks:
        if check is not None or evidence is not None:
            raise ValueError("Unexpected relationship check")
        return baseline
    if check is None:
        raise ValueError("Missing relationship evidence check")
    if evidence is not None and set(evidence) != set(asks):
        raise ValueError("Evidence must cover the original relationship clauses")
    result = json.loads(check)
    if not isinstance(result, dict) or set(result) != {"parts"} or not isinstance(result["parts"], list):
        raise ValueError("Expected relationship check parts")
    if [part.get("ask") if isinstance(part, dict) else None for part in result["parts"]] != asks:
        raise ValueError("Relationship check must cover each original clause")

    checked = []
    for part in result["parts"]:
        status = part.get("status")
        if status not in ("direct", "conditional", "insufficient") or not isinstance(part.get("ranges"), list):
            raise ValueError("Unrecognized relationship evidence status")
        fields = {"ask", "status", "ranges"} if status == "insufficient" else {"ask", "status", "ranges", "explanation"}
        if set(part) != fields or bool(part["ranges"]) != (status != "insufficient"):
            raise ValueError("Relationship status must agree with selected evidence")
        if evidence is not None:
            contract = evidence[part["ask"]]
            proof = contract["proof"]
            complete = proof is not None and any(
                isinstance(r, dict) and all(type(r.get(key)) is int for key in ("source", "start", "end"))
                and r["source"] == proof["source"] and r["start"] <= proof["start"] <= proof["end"] <= r["end"]
                for r in part["ranges"]
            )
            explanation = part.get("explanation")
            normalized = re.sub(r"\s+", "", explanation).casefold() if isinstance(explanation, str) else ""
            missing_terms = any(term.casefold() not in normalized for term in contract.get("terms", ()))
            if not complete or status != contract["status"] or missing_terms:
                checked.append({"ask": part["ask"], "ranges": contract["facts"],
                                "relation_gap": "unverified" if proof is not None else "missing"})
                continue
        final = {k: v for k, v in part.items() if k != "status"}
        if status == "conditional":
            final["qualification"] = "conditional"
        checked.append(final)

    windows = []
    offset = 0
    for ask in asks:
        start = question.index(ask, offset)
        offset = start + len(ask)
        windows.append((start, offset))
    combined = list(zip((start for start, _ in windows), checked, strict=True))
    offset = 0
    for part in json.loads(selection)["parts"]:
        start = question.index(part["ask"], offset)
        offset = start + len(part["ask"])
        if any(start < end and offset > begin for begin, end in windows):
            if not any(begin <= start and offset <= end for begin, end in windows):
                raise ValueError("Keep relationship and other question parts separate")
        else:
            combined.append((start, part))
    ordered = [part for _, part in sorted(combined, key=lambda item: item[0])]
    return render_course_question_parts(json.dumps({"parts": ordered}), question, contexts,
                                        allow_qualified=True, allow_explanation=True)
