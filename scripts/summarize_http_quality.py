"""Bind saved AI source annotations to answers; export counts without source text."""
import argparse
import hashlib
import json
from pathlib import Path

from scripts.audit_public_http import audit


def source_review_summary(cases, rows, reviews):
    case_ids = {case["id"] for case in cases}
    if not cases or len(case_ids) != len(cases):
        raise ValueError("Expected nonempty unique cases")
    indexed = {row["id"]: row for row in rows}
    reviewed = {review["id"]: review for review in reviews}
    if (len(indexed) != len(rows) or len(reviewed) != len(reviews)
            or set(indexed) - case_ids or set(reviewed) - set(indexed)):
        raise ValueError("Duplicate or unknown result/review IDs")
    for identifier, review in reviewed.items():
        answer = "".join(event.get("content", "") for event in indexed[identifier]["events"]
                         if event["type"] == "token")
        if review.get("answer_sha256") != hashlib.sha256(answer.encode()).hexdigest():
            raise ValueError("Source review belongs to a different answer")
        if (type(review.get("accepted")) is not bool or not review.get("reason_code")
                or not review.get("reviewer")):
            raise ValueError("Source annotation requires a boolean decision and attribution")
    missing = sorted(case_ids - set(reviewed))
    failed = sorted(identifier for identifier, review in reviewed.items() if not review["accepted"])
    return {"planned": len(cases), "recorded": len(rows), "reviewed": len(reviews),
            "accepted": sum(review["accepted"] for review in reviews),
            "missing_ids": missing, "failed_ids": failed,
            "all_reviewed_and_accepted": not missing and not failed,
            "independent_human_gold": False,
            "evidence_scope": "Saved AI source annotations; binding validates provenance, not semantic truth"}


def summarize(packet, run):
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    result = audit(packet, run)
    review_path = run / "source-reviews.json"
    source = source_review_summary(read(packet / "cases.json"), read(run / "results.json"),
                                   read(review_path) if review_path.exists() else [])
    return {"structural_passed": result["passed"], "execution_complete": result["execution_complete"],
            "budget_receipts_valid": result["budget_receipts_valid"], "source_review": source,
            "development_checks_passed": result["passed"] and source["all_reviewed_and_accepted"],
            "automatic_production_acceptance": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("packet", "run", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    report = summarize(args.packet, args.run)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"development_checks_passed": report["development_checks_passed"]}))
    if not report["development_checks_passed"]:
        raise SystemExit(1)
