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
