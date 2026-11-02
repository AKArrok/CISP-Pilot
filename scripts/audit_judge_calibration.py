"""Offline calibration audit: validate evidence before counting a score match."""
import argparse
import hashlib
import json
from pathlib import Path

from scripts.answer_grade_contract import materialize_grade, validate_grade


def audit(packet, output):
    cases = json.loads((packet / "cases.json").read_text(encoding="utf-8"))
    manifest = json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest.get("frozen_sha256", {}).items():
        if hashlib.sha256((packet / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Frozen input changed")
    raw_results = json.loads((output / "results.json").read_text(encoding="utf-8"))
    results = {x["id"]: x for x in raw_results}
    case_ids = {case["id"] for case in cases}
    if len(case_ids) != len(cases) or len(results) != len(raw_results) or set(results) - case_ids:
        raise ValueError("Duplicate or unknown calibration case IDs")
    arms = manifest.get("judge_arms", ["judge_baseline", "judge_candidate"])
    if not cases or not arms:
        raise ValueError("Calibration requires cases and judge arms")
    baseline = None
    if "baseline_run" in manifest:
        path = Path(__file__).resolve().parents[1] / manifest["baseline_run"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["baseline_sha256"]:
            raise ValueError("Frozen baseline changed")
        raw_baseline = json.loads(path.read_text(encoding="utf-8"))
        baseline = {x["id"]: x for x in raw_baseline}
        if len(baseline) != len(raw_baseline) or set(baseline) != case_ids:
            raise ValueError("Duplicate or unknown baseline case IDs")
    rows = []
    for case in cases:
        for arm in arms:
            row = {"id": case["id"], "arm": arm, "status": "not_completed", "passed": False}
            if case["id"] in results and arm in results[case["id"]]:
                try:
                    validator = materialize_grade if manifest.get("evidence_protocol") == "line_ids" else validate_grade
                    item = {"answer": case["answer"], "contexts": case["fixed_contexts"]}
                    value = (validator(json.loads(results[case["id"]][arm]), item, allow_absent_source=True)
                             if manifest.get("allow_absent_source") and validator is materialize_grade
                             else validator(json.loads(results[case["id"]][arm]), item))
                    if baseline is not None:
                        prior = materialize_grade(json.loads(baseline[case["id"]][arm]),
                                                  {"answer": case["answer"], "contexts": case["fixed_contexts"]})
                        value["scores"]["source_support"] = min(value["scores"]["source_support"],
                                                                prior["scores"]["source_support"])
                        value["scores"]["citation_support"] = prior["scores"]["citation_support"]
                        value["scores"]["completeness"] = prior["scores"]["completeness"]
                    row.update(status="validated", score=value["scores"][case["expected_dimension"]])
                    row["passed"] = case["minimum"] <= row["score"] <= case["maximum"]
                except (ValueError, KeyError, TypeError) as exc:
                    row.update(status="invalid_evidence_or_schema", error_type=type(exc).__name__)
            rows.append(row)
    return {"passed": all(row["passed"] for row in rows), "checks": rows,
            "arm_matches": {arm: sum(row["passed"] for row in rows if row["arm"] == arm) for arm in arms},
            "independent_human_review": False, "provider_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.packet, args.run)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": result["passed"], "arm_matches": result["arm_matches"]}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
