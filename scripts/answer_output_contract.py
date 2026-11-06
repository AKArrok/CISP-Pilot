"""Render a candidate structured answer using original source metadata only."""
import json
import re

from agents.sources import source_location


def render_structured_answer(raw: str, contexts: list[dict]) -> str:
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"answer", "source_ids"}:
        raise ValueError("Expected answer and source_ids only")
    answer, refs = value["answer"], value["source_ids"]
    if not isinstance(answer, str) or not answer.strip() or "【出处】" in answer:
        raise ValueError("Answer body must be nonempty and exclude the source footer")
    if (not isinstance(refs, list) or not refs
            or any(type(ref) is not int or not 1 <= ref <= len(contexts) for ref in refs)
            or len(set(refs)) != len(refs)):
        raise ValueError("Invalid source IDs")
    if any(int(ref) not in refs for ref in re.findall(r"\[资料(\d+)\]", answer)):
        raise ValueError("Inline citation is not in source_ids")
    sources = [f"[资料{ref}]《{contexts[ref-1]['source']}》{source_location(contexts[ref-1])}" for ref in refs]
    return answer.strip() + "\n\n【出处】" + "；".join(sources)


def render_evidenced_answer(raw: str, contexts: list[dict]) -> str:
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"answer", "source_ids", "source_lines"}:
        raise ValueError("Expected answer, source_ids and source_lines only")
    base = render_structured_answer(json.dumps({key: value[key] for key in ("answer", "source_ids")}), contexts)
    if not isinstance(value["source_lines"], list) or not value["source_lines"]:
        raise ValueError("Original evidence lines required")
    evidence, seen = [], set()
    for entry in value["source_lines"]:
        if not isinstance(entry, dict) or set(entry) != {"source_id", "line"}:
            raise ValueError("Select original line IDs, never rewrite quotes")
        ref, number = entry["source_id"], entry["line"]
        if (type(ref) is not int or ref not in value["source_ids"] or type(number) is not int
                or not 1 <= number <= len(contexts[ref-1]["text"].splitlines()) or (ref, number) in seen):
            raise ValueError("Invalid original evidence line")
        quote = contexts[ref-1]["text"].splitlines()[number-1]
        if not quote.strip():
            raise ValueError("Empty original evidence line")
        seen.add((ref, number))
        evidence.append(f"[资料{ref}]原文第{number}行\n> {quote}")
    answer, _, sources = base.rpartition("\n\n【出处】")
    return ("中文解释（非逐字原文）\n" + answer + "\n\n【原文证据】\n"
            + "\n\n".join(evidence) + "\n\n【出处】" + sources)
