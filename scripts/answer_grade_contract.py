"""Pure offline validation of score dimensions and literal evidence quotes."""
DIMENSIONS = ("completeness", "source_support", "citation_support")


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
        quote = risk.get("answer_quote")
        if not isinstance(quote, str) or not quote or quote not in item["answer"]:
            raise ValueError("Invented risk answer quote")
        source = risk.get("source_quote")
        if source == "none_found" and risk["verdict"] == "unsupported":
            continue
        if not isinstance(source, str) or not source or not any(source in c["text"] for c in item["contexts"]):
            raise ValueError("Invented risk source quote")
    return value
