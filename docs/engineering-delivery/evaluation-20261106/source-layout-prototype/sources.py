"""Source locations shared by answer prompts and citation audits."""
from __future__ import annotations

import json
import re


def source_answer_text(source: dict) -> str:
    """Lay out exported OSCAL prose and exact parameter references for answering."""
    text = source["text"]
    marker = "\nOriginal OSCAL parameter definitions (no values assigned by this extraction):\n"
    body, found, definitions = text.partition(marker)
    if not found or source.get("page") is not None:
        return text
    try:
        parameters = json.loads(definitions)
    except json.JSONDecodeError:
        return text
    if (not isinstance(parameters, list) or not parameters
            or any(not isinstance(p, dict) or not isinstance(p.get("id"), str) for p in parameters)
            or len({p["id"] for p in parameters}) != len(parameters)):
        return text
    clauses = list(re.finditer(r"(?m)^\[([^\]\n]+)\] ([^\n]*)", body))
    uses = {match[1]: re.findall(r"\{\{\s*insert:\s*param,\s*([^\s}]+)\s*\}\}", match[2])
            for match in clauses}
    bindings = [{"parameter": p, "referenced_in": [key for key, ids in uses.items() if p["id"] in ids]}
                for p in parameters if any(p["id"] in ids for ids in uses.values())]
    if not bindings:
        return text
    for match in reversed(clauses):
        if "_gdn" in match[1]:
            laid_out = re.sub(r"(?<=[.!?]) (?=[A-Z])", "\n", match[2])
            body = body[:match.start(2)] + laid_out + body[match.end(2):]
    return (body + marker + definitions
            + "\n\n原文参数引用位置（机械提取；不扩展适用范围，不赋值）：\n"
            + json.dumps(bindings, ensure_ascii=False))


def source_location(source: dict) -> str:
    if source.get("page") is not None:
        return f"第{source['page']}页"
    locator = source.get("locator", "")
    if isinstance(locator, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", locator):
        return f"定位【{locator}】"
    raise ValueError("Source requires an original page or a structured locator")


def source_preview(source: dict) -> dict:
    source_location(source)  # Reject missing locations before emitting a broken source.
    result = {"source": source["source"], "page": source.get("page"),
              "domain": source["domain"], "text": source["text"][:120]}
    if source.get("page") is None:
        result.update(locator=source["locator"], source_url=source.get("source_url"))
    return result
