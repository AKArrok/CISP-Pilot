"""Check complete answer decomposition and aggregate atomic source judgements."""
import argparse
import hashlib
import json
from pathlib import Path
import re


HEADINGS = {"结论", "依据", "依据要点", "依据说明", "具体依据", "展开说明", "要点", "要点说明",
            "具体说明", "依据展开", "依据与区分", "依据与限制", "补充要点", "易混淆点", "记忆点",
            "记忆点/易混淆点", "资料缺口", "可依据资料说明的要点"}
GENERAL = re.compile(r"（通用补充）|\(通用补充\)|\*\*通用补充\*\*")


def exclusion_kind(text):
    if text.lstrip().startswith("【出处】"):
        return "citation"
    if GENERAL.match(text.lstrip()):
        return "marked_general"
    if text.strip(" -*#`\t\r\n:：") in HEADINGS:
        return "heading"
    if re.fullmatch(r"[\s*#`_\-:：]+", text):
        return "format"
    return None


def indexed(rows, allow_empty=False):
    result = {row["id"]: row for row in rows}
    if len(result) != len(rows) or (not result and not allow_empty):
        raise ValueError("Expected nonempty unique IDs")
    return result


def audit(packet, run):
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    manifest = read(packet / "manifest.json")
    if not {"cases.json", "parent-cases.json", "excluded-spans.json", "prompts.json"} <= set(manifest["frozen_sha256"]):
        raise ValueError("Incomplete frozen decomposition")
    for name, digest in manifest["frozen_sha256"].items():
        if hashlib.sha256((packet / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Frozen input changed")
    parents = indexed(read(packet / "parent-cases.json"))
    cases = indexed(read(packet / "cases.json"))
    covered = {key: [False] * len(row["answer"]) for key, row in parents.items()}

    def cover(parent_id, start, end):
        if (parent_id not in parents or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(covered[parent_id])
                or any(covered[parent_id][start:end])):
            raise ValueError("Invalid or overlapping answer span")
        covered[parent_id][start:end] = [True] * (end - start)
        return parents[parent_id]["answer"][start:end]

    for case in cases.values():
        parent = parents.get(case["parent_id"])
        if (parent is None or case["parent_answer_sha256"] != hashlib.sha256(parent["answer"].encode()).hexdigest()
                or case["question"] != parent["question"] or case["fixed_contexts"] != parent["fixed_contexts"]
                or cover(case["parent_id"], *case["answer_span"]) != case["answer"]):
            raise ValueError("Fragment differs from original answer or evidence")
    for excluded in read(packet / "excluded-spans.json"):
        text = cover(excluded["parent_id"], excluded["start"], excluded["end"])
        if not excluded["kind"] or exclusion_kind(text) != excluded["kind"]:
            raise ValueError("Exclusion could hide a substantive claim")
    for key, parent in parents.items():
        if any(not done and not char.isspace() for done, char in zip(covered[key], parent["answer"], strict=True)):
            raise ValueError("Uncovered original answer text")
        if not any(case["parent_id"] == key for case in cases.values()):
            raise ValueError("Parent has no source claims")

    results = indexed(read(run / "results.json") if (run / "results.json").exists() else [], allow_empty=True)
    if set(results) - set(cases):
        raise ValueError("Unknown atomic result ID")
    checks = []
    scores = {"supported": 2, "uncertain": 1, "unsupported": 0}
    for key, case in cases.items():
        row = {"id": key, "parent_id": case["parent_id"], "status": "missing", "score": None}
        if key in results:
            try:
                grade = json.loads(results[key]["judge_candidate"])
                if grade["support"] not in scores or not isinstance(grade["reason"], str) or not grade["reason"].strip():
                    raise ValueError("Invalid support decision")
                ref, line = grade["source_ref"], grade["source_line"]
                if ref is None and line is None and grade["support"] != "supported":
                    quote = None
                elif (type(ref) is int and 1 <= ref <= len(case["fixed_contexts"]) and type(line) is int
                      and 1 <= line <= len(case["fixed_contexts"][ref-1]["text"].splitlines())):
                    quote = case["fixed_contexts"][ref-1]["text"].splitlines()[line-1]
                    if not quote.strip():
                        raise ValueError("Empty source line")
                else:
                    raise ValueError("Invalid source line")
                row.update(status="validated", score=scores[grade["support"]], reason=grade["reason"], source_quote=quote)
            except (KeyError, TypeError, ValueError):
                row["status"] = "invalid"
        checks.append(row)
    parent_checks = []
    for key, parent in parents.items():
        children = [row for row in checks if row["parent_id"] == key]
        valid = all(row["status"] == "validated" for row in children)
        score = min(row["score"] for row in children) if valid else None
        parent_checks.append({"id": key, "score": score, "valid": valid,
                              "passed": valid and parent["minimum"] <= score <= parent["maximum"]})
    summary = read(run / "summary.json") if (run / "summary.json").exists() else {}
    complete = (len(results) == len(cases) == summary.get("completed") == summary.get("provider_calls")
                and not summary.get("cost_unknown", True))
    return {"execution_complete": complete, "planned_atoms": len(cases), "checks": checks,
            "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "parent_checks": parent_checks, "parent_range_matches": sum(row["passed"] for row in parent_checks),
            "range_checks_passed": complete and all(row["passed"] for row in parent_checks),
            "rationale_review": "required separately; line binding does not prove entailment",
            "independent_human_gold": False, "automatic_production_acceptance": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("packet", "run", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.packet, args.run)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"parent_range_matches": result["parent_range_matches"],
                      "range_checks_passed": result["range_checks_passed"]}))
    if not result["range_checks_passed"]:
        raise SystemExit(1)
