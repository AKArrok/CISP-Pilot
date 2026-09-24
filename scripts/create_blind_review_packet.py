"""从带金标 fixture 生成不含金标和模型预测的第二审校盲审包。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fixture = json.loads(args.cases.read_text("utf-8"))
    questions = {
        question["id"]: question
        for question in json.loads((ROOT / "data/questions.json").read_text("utf-8"))
    }
    packet_cases = []
    for case in fixture["knowledge_tag_cases"]:
        question = questions[case["question_id"]]
        packet_cases.append({
            "case_id": hashlib.sha256(case["question_id"].encode("utf-8")).hexdigest()[:12],
            "question_id": case["question_id"],
            "sampling_domain": case["domain"],
            "stem": question["stem"], "options": question.get("options", {}),
            "answer": question.get("answer", ""), "analysis": question.get("analysis", ""),
            "reviewed_domain": "", "acceptable_knowledge_points": [], "review_note": "",
        })
    packet = {
        "schema_version": 1,
        "reviewer_id": "",
        "reviewed_at": "",
        "attestation": {
            "blinded_to_model_predictions": True,
            "did_not_open_gold_fixture": True,
            "independent_reviewer": True,
        },
        "instructions": (
            "逐题填写 reviewed_domain 和 acceptable_knowledge_points；不得查看模型预测或原金标。"
            "完成后填写 reviewer_id/reviewed_at，不要修改题干、选项和 case_id。"
        ),
        "cases": packet_cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(packet, ensure_ascii=False, indent=2), "utf-8")
    print(f"盲审包: {len(packet_cases)} 题 → {args.output}")


if __name__ == "__main__":
    main()
