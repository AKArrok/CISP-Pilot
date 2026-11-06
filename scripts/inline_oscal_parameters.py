"""Experimental source view: copy original definitions beside parameter uses."""
import json
import re


MARKER = "\nOriginal OSCAL parameter definitions (no values assigned by this extraction):\n"
PARAMETER = re.compile(r"\{\{\s*insert:\s*param,\s*([^\s}]+)\s*\}\}")


def expand_parameters(text):
    body, found, definitions = text.partition(MARKER)
    if not found:
        return text, []
    parameters = json.loads(definitions)
    if (not isinstance(parameters, list) or not parameters
            or any(not isinstance(p, dict) or not isinstance(p.get("id"), str) for p in parameters)
            or len({p["id"] for p in parameters}) != len(parameters)):
        raise ValueError("Expected unique original parameter definitions")
    by_id = {p["id"]: p for p in parameters}
    pieces, additions, previous, length = [], [], 0, 0
    for match in PARAMETER.finditer(body):
        if match[1] not in by_id:
            raise ValueError("Referenced parameter has no original definition")
        original = body[previous:match.end()]
        pieces.append(original)
        length += len(original)
        annotation = "（原始参数定义，不赋值或扩展范围：" + json.dumps(by_id[match[1]], ensure_ascii=False) + "）"
        pieces.append(annotation)
        additions.append({"parameter_id": match[1], "start": length,
                          "end": length + len(annotation), "inserted_text": annotation})
        length += len(annotation)
        previous = match.end()
    return "".join(pieces) + body[previous:] + MARKER + definitions, additions


def parameter_bindings(text):
    """Copy only literal parameter-to-clause references, never infer ownership."""
    body, found, definitions = text.partition(MARKER)
    if not found:
        return []
    expand_parameters(text)  # Reuse original definition and reference validation.
    parameters = json.loads(definitions)
    return [{"parameter_id": parameter["id"], "referenced_in": [
        {"line": number, "original_clause": line}
        for number, line in enumerate(body.splitlines(), 1)
        if parameter["id"] in PARAMETER.findall(line)]}
        for parameter in parameters]
