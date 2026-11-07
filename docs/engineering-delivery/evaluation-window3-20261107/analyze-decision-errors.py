"""Confusion analysis of the frozen paired review; no additional judging."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
run = ROOT / "docs/engineering-delivery/nist-decision-calibration-v2-20261107-live"
audit = json.loads((run / "calibration-audit.json").read_text(encoding="utf-8"))
counts = {"true_positive": 0, "true_negative": 0, "false_positive": 0, "false_negative": 0}
for row in audit["checks"]:
    key = ("true_positive" if row["expected"] and row["observed"] else
           "true_negative" if not row["expected"] and not row["observed"] else
           "false_positive" if row["observed"] else "false_negative")
    counts[key] += 1
report = {"positive_definition": "The saved answer is acceptable under the original development criterion",
    "labels_source": "Codex full-source review, not independent human gold",
    "n": 8, "confusion": counts,
    "known_bad_answer_detection": {"detected": counts["true_negative"], "total": counts["true_negative"] + counts["false_positive"]},
    "correct_answer_retention": {"retained": counts["true_positive"], "total": counts["true_positive"] + counts["false_negative"]},
    "exact_quote_count": audit["verbatim_evidence"],
    "bad_answers_accepted_with_exact_quotes": [row["id"] for row in audit["checks"]
        if not row["expected"] and row["observed"] and row["verbatim_evidence"]],
    "interpretation": "All4 known bad answers escaped review; valid verbatim quotes do not imply that the model's conclusion follows from them. Three wrong acceptances even have valid quotes. A same-model second opinion cannot currently guard promotion.",
    "next_contract_requirements": ["Keep source statement and original question polarity separate",
        "Check omission of explicit source obligations as well as false assertions",
        "Check every heading and background claim, not just final conclusion",
        "Keep original paired labels offline and require all8 before broad40 regression"],
    "calibration_subset_generalization_claim": False, "automatic_acceptance_enabled": False, "new_API_calls": 0}
(HERE / "decision-confusion-analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
