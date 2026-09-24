"""在两份盲审都完成后生成裁决包。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.assemble_external_holdout import _validated_review
from scripts.create_external_blind_review_packets import _digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--review-a", type=Path, required=True)
    parser.add_argument("--review-b", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.source.read_text("utf-8"))
    left = json.loads(args.review_a.read_text("utf-8"))
    right = json.loads(args.review_b.read_text("utf-8"))
    questions = source.get("questions", [])
    if any(not row.get("id") for row in questions) or len({row["id"] for row in questions}) != len(questions):
        raise ValueError("原始题源 ID 必须非空且唯一")
    source_by_id = {row["id"]: row for row in questions}
    source_hash = _digest(source.get("questions", []))
    left_rows = _validated_review(left, source_hash, source_by_id, "reviewer_a")
    right_rows = _validated_review(right, source_hash, source_by_id, "reviewer_b")
    reviewer_ids = [left["reviewer_id"], right["reviewer_id"]]
    normalized_ids = [reviewer_id.casefold() for reviewer_id in reviewer_ids]
    if len(set(normalized_ids)) != 2:
        raise ValueError("两份盲审包必须由不同审校者签署")
    review_packet_sha256 = {
        left["reviewer_id"]: _digest(left),
        right["reviewer_id"]: _digest(right),
    }
    packet = {
        "schema_version": 2,
        "adjudicator_id": "",
        "adjudicated_at": "",
        "method": "先冻结两份独立标注，再对分歧和一致项逐题确认",
        "reviewer_ids": reviewer_ids,
        "source_questions_sha256": source_hash,
        "review_packet_sha256": review_packet_sha256,
        "cases": [{
            "id": question_id,
            "stem": source_by_id[question_id]["stem"],
            "reviewed_domains": {
                left["reviewer_id"]: left_rows[question_id]["reviewed_domain"],
                right["reviewer_id"]: right_rows[question_id]["reviewed_domain"],
            },
            "review_labels": {
                left["reviewer_id"]: left_rows[question_id]["acceptable_knowledge_points"],
                right["reviewer_id"]: right_rows[question_id]["acceptable_knowledge_points"],
            },
            "adjudicated_domain": "",
            "acceptable": [],
            "note": "",
        } for question_id in source_by_id],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(packet, ensure_ascii=False, indent=2), "utf-8")
    print(f"裁决包: {len(packet['cases'])} 题 → {args.output}")


if __name__ == "__main__":
    main()
