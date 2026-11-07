"""Audit original saved-answer labels and exact evidence; never repair model output."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PACKET = ROOT / "evaluation/nist-decision-calibration-v2-20261107"
RUN = ROOT / "docs/engineering-delivery/nist-decision-calibration-v2-20261107-live"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


cases = {row["id"]: row for row in read(PACKET / "cases.json")}
labels = {row["id"]: row for row in read(PACKET / "labels-offline-only.json")}
rows = read(RUN / "results.json")
assert len(rows) == len(cases) == len(labels) == 8 and len({row["id"] for row in rows}) == 8
checks = []
for row in rows:
    case, label = cases[row["id"]], labels[row["id"]]
    assert hashlib.sha256(row["answer"].encode()).hexdigest() == label["answer_sha256"]
    assert row["answer"] == case["answer"] and row["question"] == case["question"]
    judgment = json.loads(row["judge_candidate"])
    ref, quote = judgment.get("source_ref"), judgment.get("source_quote")
    evidence = (type(ref) is int and 1 <= ref <= len(case["fixed_contexts"])
                and isinstance(quote, str) and bool(quote) and quote in case["fixed_contexts"][ref - 1]["text"])
    label_match = type(judgment.get("acceptable")) is bool and judgment["acceptable"] == label["acceptable"]
    checks.append({"id": row["id"], "original_case": label["original_case"], "expected": label["acceptable"],
        "observed": judgment.get("acceptable"), "label_match": label_match, "verbatim_evidence": evidence,
        "extra_fields": sorted(set(judgment) - {"acceptable", "reason", "source_ref", "source_quote"}),
        "passed": label_match and evidence, "reason": judgment.get("reason")})
summary = read(RUN / "summary.json")
report = {"passed": all(row["passed"] for row in checks), "label_matches": sum(row["label_match"] for row in checks),
    "verbatim_evidence": sum(row["verbatim_evidence"] for row in checks), "joint_pass": sum(row["passed"] for row in checks),
    "total": 8, "calls": summary["provider_calls"], "cost_estimate_usd": summary["cost_usd_conservative"],
    "scope": "Seen matched development answers, Codex labels; no human gold, no production auto-acceptance",
    "checks": checks, "next": "Do not enable this verifier or continue prompt-only tuning: same polarity reversal and source-obligation incompleteness escaped review. Separate source claims from user propositions in next frozen design; keep all original criteria."}
write(RUN / "calibration-audit.json", report)
statefile = HERE / "continuation.json"
state = read(statefile)
state["decision_calibration"] = {key: value for key, value in report.items() if key != "checks"}
state["decision_calibration"]["run"] = RUN.relative_to(ROOT).as_posix()
state["next"] = "Finish exact route JSON retest/audit, publish precise commit and CI; then redesign question-polarity/source-claim separation without repeated prompt-only variants. Boundary12/private4 remain gated."
write(statefile, state)
print(json.dumps({key: report[key] for key in ("passed", "label_matches", "verbatim_evidence", "joint_pass", "total")}))
