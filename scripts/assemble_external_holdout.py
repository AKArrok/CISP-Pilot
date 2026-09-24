"""合并两份已签署盲审包和人工裁决，不运行任何模型预测。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.create_external_blind_review_packets import _digest


ATTESTATIONS = (
    "blinded_to_predictions", "blinded_to_other_reviewer",
    "did_not_access_development_sets", "independent_human_reviewer",
)


def _validated_review(
    review: dict,
    source_hash: str,
    source_by_id: dict[str, dict],
    reviewer_slot: str,
) -> dict[str, dict]:
    if review.get("schema_version") != 1 or review.get("reviewer_slot") != reviewer_slot:
        raise ValueError(f"盲审包槽位或版本无效: {reviewer_slot}")
    if review.get("source_questions_sha256") != source_hash:
        raise ValueError("盲审包与原始题源哈希不一致")
    reviewer_id = review.get("reviewer_id")
    reviewed_at = review.get("reviewed_at")
    if (
        not isinstance(reviewer_id, str) or not reviewer_id.strip()
        or reviewer_id != reviewer_id.strip()
        or not isinstance(reviewed_at, str) or not reviewed_at.strip()
    ):
        raise ValueError("盲审包未填写有效 reviewer_id/reviewed_at")
    if not all(review.get("attestation", {}).get(key) is True for key in ATTESTATIONS):
        raise ValueError("盲审独立性声明不完整")
    case_list = review.get("cases", [])
    if not isinstance(case_list, list) or not all(isinstance(row, dict) for row in case_list):
        raise ValueError("盲审包题目列表结构无效")
    rows = {row.get("id"): row for row in case_list}
    if len(rows) != len(case_list) or set(rows) != set(source_by_id):
        raise ValueError("盲审包题目集有重复 ID 或与原始题源不一致")
    immutable = ("id", "stem", "options", "answer", "analysis")
    if any(
        any(row.get(field, "") != source_by_id[question_id].get(field, "")
            for field in immutable)
        for question_id, row in rows.items()
    ):
        raise ValueError("盲审包的题干、选项、答案或解析被修改")
    if not all(
        isinstance(row.get("reviewed_domain"), str)
        and row["reviewed_domain"].strip()
        and isinstance(row.get("acceptable_knowledge_points"), list)
        and row["acceptable_knowledge_points"]
        and all(
            isinstance(point, str) and point.strip()
            for point in row["acceptable_knowledge_points"]
        )
        and len(set(row["acceptable_knowledge_points"]))
        == len(row["acceptable_knowledge_points"])
        for row in rows.values()
    ):
        raise ValueError("盲审包存在未标注或重复标签题目")
    return rows

def assemble(source: dict, left: dict, right: dict, adjudication: dict) -> dict:
    questions = source.get("questions", [])
    if (
        not isinstance(questions, list)
        or any(not isinstance(row, dict) or not row.get("id") for row in questions)
        or len({row["id"] for row in questions}) != len(questions)
    ):
        raise ValueError("原始题源 ID 必须非空且唯一")
    source_hash = _digest(questions)
    source_by_id = {row["id"]: row for row in questions}
    source_ids = set(source_by_id)
    left_rows = _validated_review(left, source_hash, source_by_id, "reviewer_a")
    right_rows = _validated_review(right, source_hash, source_by_id, "reviewer_b")
    reviewer_ids = [left["reviewer_id"], right["reviewer_id"]]
    if len({reviewer_id.casefold() for reviewer_id in reviewer_ids}) != 2:
        raise ValueError("两份盲审包必须由不同审校者签署")

    if adjudication.get("schema_version") != 2:
        raise ValueError("裁决包版本无效")
    if adjudication.get("source_questions_sha256") != source_hash:
        raise ValueError("裁决包与原始题源哈希不一致")
    if adjudication.get("reviewer_ids") != reviewer_ids:
        raise ValueError("裁决包审校者与冻结盲审包不一致")
    review_packet_sha256 = {
        reviewer_ids[0]: _digest(left),
        reviewer_ids[1]: _digest(right),
    }
    if adjudication.get("review_packet_sha256") != review_packet_sha256:
        raise ValueError("裁决包与冻结审校包哈希不一致")
    if not all(adjudication.get(key) for key in ("adjudicator_id", "adjudicated_at", "method")):
        raise ValueError("缺少裁决人、时间或方法")

    decision_list = adjudication.get("cases", [])
    if not isinstance(decision_list, list) or not all(
        isinstance(row, dict) for row in decision_list
    ):
        raise ValueError("裁决题目列表结构无效")
    decisions = {row.get("id"): row for row in decision_list}
    if len(decisions) != len(decision_list) or set(decisions) != source_ids:
        raise ValueError("裁决文件存在重复题目或未覆盖全部题目")

    merged = []
    for question_id, question in source_by_id.items():
        decision = decisions[question_id]
        expected_labels = {
            reviewer_ids[0]: left_rows[question_id]["acceptable_knowledge_points"],
            reviewer_ids[1]: right_rows[question_id]["acceptable_knowledge_points"],
        }
        expected_domains = {
            reviewer_ids[0]: left_rows[question_id]["reviewed_domain"],
            reviewer_ids[1]: right_rows[question_id]["reviewed_domain"],
        }
        if (
            decision.get("stem") != question.get("stem")
            or decision.get("review_labels") != expected_labels
            or decision.get("reviewed_domains") != expected_domains
        ):
            raise ValueError(f"裁决包中的独立标注与冻结盲审包不一致: {question_id}")
        acceptable = decision.get("acceptable")
        if (
            not isinstance(decision.get("adjudicated_domain"), str)
            or not decision["adjudicated_domain"].strip()
            or not isinstance(acceptable, list)
            or not acceptable
            or not all(isinstance(point, str) and point.strip() for point in acceptable)
            or len(set(acceptable)) != len(acceptable)
        ):
            raise ValueError(f"裁决不完整: {question_id}")
        merged.append({
            **question,
            "review_labels": expected_labels,
            "reviewed_domains": expected_domains,
            "adjudicated_domain": decision["adjudicated_domain"],
            "acceptable": acceptable,
            "adjudication_note": decision.get("note", ""),
        })
    result = {
        "schema_version": 2,
        "dataset_role": "外部来源、双人盲审、从未用于开发的最终验收集",
        "source": source["source"],
        "reviewers": [{
            "reviewer_id": reviewer_ids[index],
            "reviewed_at": review["reviewed_at"],
            "blinded_to_predictions": True,
            "blinded_to_other_reviewer": True,
            "did_not_access_development_sets": True,
            "independent_human_reviewer": True,
            "review_packet_sha256": review_packet_sha256[reviewer_ids[index]],
        } for index, review in enumerate((left, right))],
        "adjudication": {
            "schema_version": 2,
            "source_questions_sha256": source_hash,
            "reviewer_ids": reviewer_ids,
            "review_packet_sha256": review_packet_sha256,
            "adjudicator_id": adjudication["adjudicator_id"],
            "adjudicated_at": adjudication["adjudicated_at"],
            "method": adjudication["method"],
        },
        "quality_gates": {
            "primary_tag_coverage_min": 0.80,
            "primary_tag_accuracy_min": 0.80,
            "review_label_overlap_min": 0.80,
        },
        "questions": merged,
    }
    payload = json.dumps(merged, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    result["frozen_cases_sha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return result

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--review-a", type=Path, required=True)
    parser.add_argument("--review-b", type=Path, required=True)
    parser.add_argument("--adjudication", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = assemble(*[
        json.loads(path.read_text("utf-8"))
        for path in (args.source, args.review_a, args.review_b, args.adjudication)
    ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    print(f"外部 holdout 已冻结: {len(result['questions'])} 题 → {args.output}")


if __name__ == "__main__":
    main()
