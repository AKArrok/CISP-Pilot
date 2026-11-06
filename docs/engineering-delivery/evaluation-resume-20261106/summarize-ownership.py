"""Reconcile all four native-ownership batches, including repeat failures."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.answer_output_contract import render_evidenced_answer  # noqa: E402


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


records, original = [], None
for stem in ("nist-native-ownership", "nist-native-ownership-repeat",
             "nist-native-ownership-noaid", "nist-native-ownership-noaid-repeat"):
    directory = ROOT / "docs/engineering-delivery" / (stem + "-20261107-live")
    rows, reviews, summary = [read(directory / name) for name in ("results.json", "source-reviews.json", "summary.json")]
    inputs = [{"id": row["id"], "question": row["question"], "contexts": row["contexts"]} for row in rows]
    if original is None:
        original = inputs
    assert inputs == original
    grades = {row["id"]: row for row in reviews["candidate_reviews"]}
    for row in rows:
        grade = grades[row["id"]]
        assert grade["candidate_sha256"] == hashlib.sha256(row["answer_structured"].encode()).hexdigest()
        assert grade["rendered_candidate"] == render_evidenced_answer(row["answer_structured"], row["contexts"])
    receipts = [json.loads(line) for line in (directory / "receipts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(receipts) == summary["provider_calls"] == 16 and len(rows) == 8
    cost = sum((row["response"]["usage"]["prompt_tokens"] * .3
                + row["response"]["usage"]["completion_tokens"] * 1.2) / 1e6 for row in receipts)
    assert abs(cost - summary["cost_usd_conservative"]) < 1e-10
    records.append({"batch": directory.relative_to(ROOT).as_posix(), "cases": 8,
        "AI_supported": sum(row["accepted"] for row in grades.values()),
        "literal_provenance": 8, "calls_including_baseline": len(receipts),
        "cost_conservative_usd": round(cost, 10),
        "failures": [{"id": row["id"], "reason": row["reason"]} for row in grades.values() if not row["accepted"]]})
report = {"scope": "Original8 repeated four times, not32independent cases or human gold",
    "inputs_identical_questions_and_English_sources": True, "records": records,
    "candidate_observations": 32, "candidate_supported_AI": sum(row["AI_supported"] for row in records),
    "literal_provenance_valid": 32, "total_calls_including_baselines": 64,
    "cost_conservative_usd": round(sum(row["cost_conservative_usd"] for row in records), 10),
    "decision": "Neither aided nor noaid candidate passes repeat stability; keep default. No causal/statistical generalization or best-run selection.",
    "next": "Source-derived fact units and terminology provenance; retain AC8/AT2/CP9 failures. Repeat/negative/full32/mixed4 gates before default promotion."}
Path(__file__).with_name("ownership-ablation-summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: report[k] for k in ("candidate_observations", "candidate_supported_AI", "literal_provenance_valid", "total_calls_including_baselines", "cost_conservative_usd")}))
