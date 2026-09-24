"""独立题目标签 holdout：与人工覆盖和校准集严格隔离。"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_ingest.tag_review import MANUAL_PRIMARY
from tests.eval_profile_accuracy import evaluate_knowledge_tags


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "tests/fixtures/profile_accuracy_holdout.json"
DEFAULT_REPORT = ROOT / "data/profile_accuracy_holdout_report.json"
CALIBRATION = ROOT / "tests/fixtures/profile_accuracy_cases.json"


def validate_holdout(cases: dict) -> dict:
    frozen_payload = json.dumps(
        cases["knowledge_tag_cases"], ensure_ascii=False,
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    actual_hash = hashlib.sha256(frozen_payload).hexdigest()
    questions = {
        question["id"]: question
        for question in json.loads((ROOT / "data/questions.json").read_text("utf-8"))
    }
    calibration_ids = {
        case["question_id"]
        for case in json.loads(CALIBRATION.read_text("utf-8"))["knowledge_tag_cases"]
    }
    ids = [case["question_id"] for case in cases["knowledge_tag_cases"]]
    missing = sorted(set(ids) - questions.keys())
    dirty = sorted(
        question_id for question_id in ids
        if question_id in questions and (
            questions[question_id].get("needs_review") or questions[question_id].get("dup_of")
        )
    )
    domain_counts = Counter(case["domain"] for case in cases["knowledge_tag_cases"])
    return {
        "unique_ids": len(ids) == len(set(ids)),
        "frozen_cases_unchanged": actual_hash == cases["frozen_cases_sha256"],
        "missing_question_ids": missing,
        "review_or_duplicate_ids": dirty,
        "calibration_overlap": sorted(set(ids) & calibration_ids),
        "manual_override_overlap": sorted(set(ids) & set(MANUAL_PRIMARY)),
        "domain_counts": dict(sorted(domain_counts.items())),
        "stratified": (
            len(domain_counts) == 10
            and set(domain_counts.values()) == {cases["sample_per_domain"]}
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text("utf-8"))
    integrity = validate_holdout(cases)
    scores = evaluate_knowledge_tags(cases)
    cases_by_domain = defaultdict(list)
    for case in cases["knowledge_tag_cases"]:
        cases_by_domain[case["domain"]].append(case)
    by_domain = {
        domain: {
            key: value for key, value in evaluate_knowledge_tags(
                {"knowledge_tag_cases": domain_cases}
            ).items()
            if key in {
                "sample_size", "primary_tag_coverage", "gold_taxonomy_coverage",
                "conditional_accuracy", "primary_tag_accuracy", "correct",
            }
        }
        for domain, domain_cases in sorted(cases_by_domain.items())
    }
    gates = cases["quality_gates"]
    gate_results = {
        "dataset_integrity": (
            integrity["unique_ids"] and integrity["frozen_cases_unchanged"]
            and integrity["stratified"]
            and not integrity["missing_question_ids"]
            and not integrity["review_or_duplicate_ids"]
            and not integrity["calibration_overlap"]
            and not integrity["manual_override_overlap"]
        ),
        "primary_tag_coverage": scores["primary_tag_coverage"] >= gates["primary_tag_coverage_min"],
        "primary_tag_accuracy": scores["primary_tag_accuracy"] >= gates["primary_tag_accuracy_min"],
        "parse_contamination": scores["parse_contamination_rate"] <= gates["parse_contamination_max"],
    }
    report = {
        "dataset": {
            "schema_version": cases["schema_version"],
            "dataset_role": cases["dataset_role"],
            "sampling_seed": cases["sampling_seed"],
            "sample_size": len(cases["knowledge_tag_cases"]),
        },
        "integrity": integrity,
        "knowledge_tags": scores,
        "by_domain": by_domain,
        "quality_gates": {
            "thresholds": gates,
            "results": gate_results,
            "passed": all(gate_results.values()),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"报告: {args.output}")
    return 1 if args.enforce and not report["quality_gates"]["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
