"""Pure offline validation of score dimensions and literal evidence quotes."""
from copy import deepcopy

DIMENSIONS = ("completeness", "source_support", "citation_support")


def line_payload(item):
    """Number original lines without changing their content or exposing labels."""
    def numbered(text):
        return [{"line": i, "text": line} for i, line in enumerate(text.splitlines(), 1)]
    return {"question": item["question"], "answer_lines": numbered(item["answer"]),
            "contexts": [{"source_ref": i, "source": c["source"], "locator": c.get("locator"), "page": c.get("page"),
                          "lines": numbered(c["text"])} for i, c in enumerate(item["contexts"], 1)]}


def materialize_grade(value, item, *, allow_absent_source=False):
    """Resolve model-selected line IDs locally; the model never rewrites quotes."""
    result = deepcopy(value)
    for field in ("deductions", "risk_checks"):
        for entry in result.get(field, []):
            if "answer_quote" in entry or "source_quote" in entry:
                raise ValueError("Quotes must be resolved by the host")
            answer_line, source_ref, source_line = entry.pop("answer_line"), entry["source_ref"], entry.pop("source_line")
            answer_lines = item["answer"].splitlines()
            if answer_line is None and field == "deductions" and entry.get("dimension") == "completeness":
                entry["answer_quote"] = ""
            elif type(answer_line) is int and 1 <= answer_line <= len(answer_lines):
                entry["answer_quote"] = answer_lines[answer_line-1]
            else:
                raise ValueError("Invalid answer line")
            if allow_absent_source and source_ref is None and source_line is None:
                entry["source_quote"] = "none_found"
                continue
            if type(source_ref) is not int or not 1 <= source_ref <= len(item["contexts"]):
                raise ValueError("Invalid source reference")
            source_lines = item["contexts"][source_ref-1]["text"].splitlines()
            if source_line is None:
                entry["source_quote"] = "none_found"
            elif type(source_line) is int and 1 <= source_line <= len(source_lines) and source_lines[source_line-1].strip():
                entry["source_quote"] = source_lines[source_line-1]
            else:
                raise ValueError("Invalid source line")
    return validate_grade(result, item)


def validate_grade(value, item):
    if not isinstance(value, dict) or set(value.get("scores", {})) != set(DIMENSIONS):
        raise ValueError("Missing dimensions")
    for score in value["scores"].values():
        if type(score) is not int or score not in (0, 1, 2):
            raise ValueError("Scores must be integers in [0,2]")
    if not isinstance(value.get("reason"), str) or not value["reason"].strip():
        raise ValueError("Missing reason")
    deductions = value.get("deductions")
    if not isinstance(deductions, list):
        raise ValueError("Missing deductions")
    for dim in DIMENSIONS:
        if value["scores"][dim] < 2 and not any(d.get("dimension") == dim for d in deductions):
            raise ValueError("Deduction lacks evidence")
    for deduction in deductions:
        if deduction.get("dimension") not in DIMENSIONS or not deduction.get("reason"):
            raise ValueError("Invalid deduction")
        quote = deduction.get("answer_quote")
        if not isinstance(quote, str) or (not quote and deduction["dimension"] != "completeness") or quote not in item["answer"]:
            raise ValueError("Invented answer quote")
        source = deduction.get("source_quote")
        ref = deduction.get("source_ref")
        if source != "none_found":
            if (type(ref) is not int or not 1 <= ref <= len(item["contexts"]) or
                    not isinstance(source, str) or not source or source not in item["contexts"][ref-1]["text"]):
                raise ValueError("Invented source quote")
    risks = value.get("risk_checks", [])
    if not isinstance(risks, list):
        raise ValueError("Invalid risk checks")
    for risk in risks:
        if risk.get("verdict") not in ("supported", "unsupported", "contradicted"):
            raise ValueError("Invalid risk verdict")
        if (risk.get("dimension") == "source_support" and risk["verdict"] == "contradicted"
                and value["scores"]["source_support"] == 2):
            raise ValueError("risk verdict conflicts with full source score")
        quote = risk.get("answer_quote")
        if not isinstance(quote, str) or not quote or quote not in item["answer"]:
            raise ValueError("Invented risk answer quote")
        source = risk.get("source_quote")
        if source == "none_found" and risk["verdict"] == "unsupported":
            continue
        if not isinstance(source, str) or not source or not any(source in c["text"] for c in item["contexts"]):
            raise ValueError("Invented risk source quote")
    return value
