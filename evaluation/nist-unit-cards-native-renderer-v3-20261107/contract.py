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


CARD_LEADS = {
    "affirm": "可以作肯定回答，但须保留下列适用条件。",
    "deny": "资料不支持题述说法，具体要求如下。",
    "not_specified": "本次资料未作出题述规定。",
    "explain": "依据资料，可作如下说明。",
    "insufficient": "本次检索到的资料不足以回答这一部分。",
}


def _statement_tag(unit):
    match = re.match(r"\[([^]]+_smt(?:\.[^]]+)?)\]", unit["text"])
    return match[1] if match else None


def materialize_card_selection(raw, catalog, contexts):
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != {"unit_ids", "answer_type"}
            or not isinstance(value["answer_type"], str) or value["answer_type"] not in CARD_LEADS):
        raise ValueError("Select original units and an allowed answer type only")
    requested = _ids(value["unit_ids"], catalog, value["answer_type"] == "insufficient")
    tags = {(unit["source_id"], _statement_tag(unit)): key for key, unit in catalog.items() if _statement_tag(unit)}
    expanded = []
    for key in requested:
        unit, ancestors = catalog[key], []
        tag = _statement_tag(unit)
        while tag and "." in tag:
            tag = tag.rsplit(".", 1)[0]
            parent = tags.get((unit["source_id"], tag))
            if parent:
                ancestors.insert(0, parent)
        expanded.extend([*ancestors, key])
    expanded = list(dict.fromkeys(expanded))
    selected = materialize_selection(json.dumps({"unit_ids": expanded,
        "insufficient": value["answer_type"] == "insufficient"}), catalog, contexts)
    return {**selected, "answer_type": value["answer_type"], "requested_unit_ids": requested,
            "ancestor_unit_ids": [key for key in expanded if key not in requested]}


def render_card_answer(selected, contexts, translations, *, question=None):
    lead = CARD_LEADS[selected["answer_type"]]
    if selected["answer_type"] == "insufficient" and question:
        lead = f"针对问题「{question}」，本次检索到的资料不足以给出有依据的回答。"
    if not selected["units"]:
        return lead
    pieces, refs = [lead, "以下辅助译文由AI核对，属于非官方译文；英文原文为来源依据。"], []
    if any(_statement_tag(unit) for unit in selected["units"]):
        pieces.append("控制条款须按原文适用条件执行；参数值待定不表示动作可选，不能据此省略原文明确要求的动作。")
    for unit in selected["units"]:
        ref = unit["source_id"]
        digest = hashlib.sha256(contexts[ref-1]["text"].encode()).hexdigest()
        key = f"{digest}:{unit['line']}:{unit['part']}"
        translation = translations["units"].get(key)
        if (translation is None or translation["english_sha256"] != hashlib.sha256(unit["text"].encode()).hexdigest()
                or not isinstance(translation.get("reading_translation"), str) or not translation["reading_translation"].strip()):
            raise ValueError("Missing or incorrectly bound source translation")
        refs.append(ref)
        pieces.append(f"[资料{ref}]原文第{unit['line']}行片段{unit['part']}\n辅助译文：{translation['reading_translation']}\n原文：\n> {unit['text']}")
    for entry in selected["parameter_definitions"]:
        ref, definition = entry["source_id"], entry["original_definition"]
        native_table = contexts[ref-1]["text"].partition(MARKER)[2].strip()
        if any(unit["source_id"] == ref and unit["text"].strip() == native_table for unit in selected["units"]):
            continue
        translation = translations["parameters"].get(definition["id"])
        digest = hashlib.sha256(contexts[ref-1]["text"].encode()).hexdigest()
        if (translation is None or translation["original_definition"] != definition
                or translation["source_text_sha256"] != digest
                or not isinstance(translation.get("reading_translation"), str) or not translation["reading_translation"].strip()):
            raise ValueError("Missing or incorrectly bound parameter translation")
        pieces.append(f"[资料{ref}]参数{definition['id']}（未赋值），引用于{', '.join(entry['referenced_unit_ids'])}\n"
                      f"辅助译文：{translation['reading_translation']}\n原生定义（结构化摘录）：{json.dumps(definition, ensure_ascii=False)}")
    return render_structured_answer(json.dumps({"answer": "\n\n".join(pieces),
        "source_ids": list(dict.fromkeys(refs))}), contexts)
