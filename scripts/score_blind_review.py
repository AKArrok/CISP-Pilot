"""校验第二审校盲审包并计算与第一版金标的一致率。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    gold = json.loads(args.gold.read_text("utf-8"))
    review = json.loads(args.review.read_text("utf-8"))
    attest = review.get("attestation", {})
    complete = bool(review.get("reviewer_id") and review.get("reviewed_at"))
    complete = complete and all(attest.get(key) is True for key in (
        "blinded_to_model_predictions", "did_not_open_gold_fixture", "independent_reviewer",
    ))
    gold_by_id = {case["question_id"]: case for case in gold["knowledge_tag_cases"]}
    rows = []
    for case in review.get("cases", []):
        labels = set(case.get("acceptable_knowledge_points", []))
        reference = set(gold_by_id.get(case["question_id"], {}).get("acceptable", []))
        if not labels:
            complete = False
        rows.append({
            "question_id": case["question_id"], "review_labels": sorted(labels),
            "reference_labels": sorted(reference), "overlap": bool(labels & reference),
            "exact": labels == reference,
        })
    overlap = sum(row["overlap"] for row in rows)
    exact = sum(row["exact"] for row in rows)
    report = {
        "complete": complete, "reviewer_id": review.get("reviewer_id", ""),
        "sample_size": len(rows),
        "label_overlap_rate": round(overlap / len(rows), 4) if rows else 0.0,
        "exact_agreement_rate": round(exact / len(rows), 4) if rows else 0.0,
        "disagreements": [row for row in rows if not row["overlap"]],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
