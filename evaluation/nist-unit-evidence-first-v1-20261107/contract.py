"""Experimental evidence-first contract; structure never proves semantic support."""
import hashlib
import json
import re

from scripts.answer_output_contract import render_structured_answer
from scripts.inline_oscal_parameters import MARKER, PARAMETER, parameter_bindings


def build_catalog(contexts, sources):
    originals = {(source["source_url"], source["locator"], source["canonical_text_sha256"]): source for source in sources}
    catalog = {}
    for ref, context in enumerate(contexts, 1):
        digest = hashlib.sha256(context["text"].encode()).hexdigest()
        source = originals.get((context["source_url"], context["locator"], digest))
        if source is None:
            raise ValueError("Original source binding mismatch")
        lines, coverage = context["text"].splitlines(), {}
        for unit in source["units"]:
            line, part, start, end = (unit[key] for key in ("parent_line", "part", "start_codepoint", "end_codepoint"))
            if (any(type(number) is not int for number in (line, part, start, end))
                    or not 1 <= line <= len(lines) or part < 1 or not 0 <= start <= end <= len(lines[line-1])
                    or unit["text"] != lines[line-1][start:end] or unit["id"] != f"{digest}:{line}:{part}"):
                raise ValueError("Invalid literal unit or offset")
            if not unit["text"].strip():
                if lines[line-1].strip():
                    raise ValueError("Empty unit cannot replace original content")
                continue
            previous = coverage.get(line, (0, 0))
            if (part, start) != (previous[0] + 1, previous[1]):
                raise ValueError("Unit sequence has gaps or overlaps")
            coverage[line] = (part, end)
            key = f"s{ref}:l{line}:p{part}"
            catalog[key] = {"id": key, "source_id": ref, "line": line, "part": part, "text": unit["text"]}
        if ({line for line, text in enumerate(lines, 1) if text.strip()} != set(coverage)
                or any(end != len(lines[line-1]) for line, (_, end) in coverage.items())):
            raise ValueError("Incomplete original unit coverage")
    return catalog


def _ids(value, available, allow_empty):
    if (not isinstance(value, list) or (not value and not allow_empty)
            or any(not isinstance(key, str) or key not in available for key in value)
            or len(set(value)) != len(value)):
        raise ValueError("Invalid, repeated or unavailable unit IDs")
    return value


def materialize_selection(raw, catalog, contexts):
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != {"unit_ids", "insufficient"}
            or type(value["insufficient"]) is not bool):
        raise ValueError("Select unit IDs and explicit evidence sufficiency only")
    keys = _ids(value["unit_ids"], catalog, value["insufficient"])
    units = [catalog[key] for key in keys]
    definitions = []
    for ref, context in enumerate(contexts, 1):
        references = {parameter for unit in units if unit["source_id"] == ref for parameter in PARAMETER.findall(unit["text"])}
        if not references:
            continue
        parameter_bindings(context["text"])  # Validate native references without adding unselected clauses.
        _, found, raw_parameters = context["text"].partition(MARKER)
        if not found:
            raise ValueError("Selected parameter has no original definition")
        parameters = {parameter["id"]: parameter for parameter in json.loads(raw_parameters)}
        for parameter in sorted(references):
            if parameter not in parameters:
                raise ValueError("Unknown native parameter reference")
            definitions.append({"source_id": ref, "original_definition": parameters[parameter],
                "referenced_unit_ids": [unit["id"] for unit in units if unit["source_id"] == ref
                                        and parameter in PARAMETER.findall(unit["text"])]})
    return {"units": units, "parameter_definitions": definitions, "insufficient": value["insufficient"]}


def render_unit_answer(raw, selected, contexts):
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"answer", "unit_ids"}:
        raise ValueError("Answer and selected unit IDs only; never provide quotes")
    answer = value["answer"]
    if not isinstance(answer, str) or not answer.strip() or "【出处】" in answer:
        raise ValueError("Nonempty answer without model-generated footer required")
    available = {unit["id"]: unit for unit in selected["units"]}
    keys = _ids(value["unit_ids"], available, selected["insufficient"])
    if not keys:
        if re.search(r"\[资料\d+\]", answer):
            raise ValueError("Refusal cannot invent a source citation")
        return "资料不足说明\n" + answer.strip()
    units = [available[key] for key in keys]
    refs = list(dict.fromkeys(unit["source_id"] for unit in units))
    base = render_structured_answer(json.dumps({"answer": answer, "source_ids": refs}), contexts)
    body, _, footer = base.rpartition("\n\n【出处】")
    quotes = [f"[资料{unit['source_id']}]原文第{unit['line']}行片段{unit['part']}\n> {unit['text']}" for unit in units]
    return "中文解释（非逐字原文）\n" + body + "\n\n【原文证据】\n" + "\n\n".join(quotes) + "\n\n【出处】" + footer
