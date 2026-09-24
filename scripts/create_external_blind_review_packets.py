"""从未标注的外部题源生成两份顺序不同的人工盲审包。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_FIELDS = {
    "acceptable", "adjudicated_domain", "model_prediction", "predicted_domain",
    "review_labels", "reviewed_domain", "acceptable_knowledge_points", "review_note",
    "reviewed_domains", "adjudication_note",
}


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_packets(source: dict, taxonomy: list[dict] | None = None) -> dict[str, dict]:
    questions = source.get("questions", [])
    if len(questions) < 100:
        raise ValueError("外部 holdout 候选题必须至少 100 道")
    ids = [question.get("id") for question in questions]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("外部题目 ID 必须非空且唯一")
    leaked = sorted({field for row in questions for field in FORBIDDEN_FIELDS if field in row})
    if leaked:
        raise ValueError(f"原始题源包含标签或预测字段: {leaked}")
    source_hash = _digest(questions)
    if taxonomy is None:
        taxonomy = json.loads(
            (ROOT / "data/knowledge_taxonomy.json").read_text("utf-8")
        )
    rubric = [{"domain": point["domain"], "name": point["name"]} for point in taxonomy
              if point.get("status") == "active"]
    packets = {}
    for slot in ("reviewer_a", "reviewer_b"):
        ordered = sorted(
            questions,
            key=lambda row: hashlib.sha256(f"{slot}:{row['id']}".encode()).hexdigest(),
        )
        packets[slot] = {
            "schema_version": 1,
            "reviewer_slot": slot,
            "reviewer_id": "",
            "reviewed_at": "",
            "source_questions_sha256": source_hash,
            "attestation": {
                "blinded_to_predictions": True,
                "blinded_to_other_reviewer": True,
                "did_not_access_development_sets": True,
                "independent_human_reviewer": True,
            },
            "instructions": (
                "独立填写每题 reviewed_domain 和 acceptable_knowledge_points。"
                "不得查看模型预测、开发集或另一位审校者结果；"
                "不得修改题目字段。"
            ),
            "knowledge_point_rubric": rubric,
            "cases": [{
                **question,
                "reviewed_domain": "",
                "acceptable_knowledge_points": [],
                "review_note": "",
            } for question in ordered],
        }
    return packets


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    packets = create_packets(json.loads(args.source.read_text("utf-8")))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for slot, packet in packets.items():
        path = args.output_dir / f"external_holdout_{slot}.json"
        path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), "utf-8")
        print(f"{slot}: {len(packet['cases'])} 题 → {path}")


if __name__ == "__main__":
    main()
