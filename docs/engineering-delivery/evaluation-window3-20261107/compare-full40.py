"""Paired unchanged-question comparison; no model calls or label rewriting."""
import hashlib
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
LIVE = ROOT / "docs/engineering-delivery"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def cohort(name):
    run = LIVE / name
    rows = read(run / "results.json")
    review = read(run / "semantic-review.json")
    audit = read(run / "audit.json")
    summary = read(run / "summary.json")
    semantics = {row["id"]: row["source_supported"] for row in review["cases"]}
    strict = {row["id"]: row["passed"] for row in audit["checks"]}
    receipts = [json.loads(line) for line in (run / "receipts.jsonl").read_text(encoding="utf-8").splitlines()]
    selectors = [row for row in receipts if row["stage"] == "answer"]
    assert len(rows) == len(semantics) == len(strict) == len(selectors) == 40
    partial_unknown, full_abstention = [], []
    for row, receipt in zip(rows, selectors, strict=True):
        selected = json.loads(receipt["response"]["generations"][0][0]["message"]["content"])["parts"]
        if any(part["decision"] == "undetermined" for part in selected):
            partial_unknown.append(row["id"])
        if all(part["decision"] in {"context", "undetermined"} and not part["unit_ids"] for part in selected):
            full_abstention.append(row["id"])
    times = sorted(row["elapsed_seconds"] for row in rows)
    chars = [len("".join(event["content"] for event in row["events"] if event["type"] == "token")) for row in rows]
    metrics = {"n": 40, "source_supported": sum(semantics.values()), "strict_pass": sum(strict.values()),
        "joint_pass": sum(semantics[key] and strict[key] for key in semantics),
        "partial_unknown_ids": partial_unknown, "full_abstention_ids": full_abstention,
        "completed_http_requests": summary["completed_http_requests"], "cost_estimate_usd": summary["known_cost_estimate_usd"],
        "latency_seconds": {"mean": statistics.mean(times), "p50": statistics.median(times),
            "p95_nearest_rank": times[math.ceil(.95 * len(times)) - 1], "max": max(times)},
        "answer_characters_mean": statistics.mean(chars), "results_sha256": hashlib.sha256((run / "results.json").read_bytes()).hexdigest()}
    return {row["id"]: row for row in rows}, semantics, metrics


old, old_semantics, old_metrics = cohort("nist-public-http-question-parts-regression40-v2-20261107-live")
new, new_semantics, new_metrics = cohort("nist-public-http-question-groups-regression40-v3-20261107-live")
assert old.keys() == new.keys()
assert all(old[key]["question"] == new[key]["question"] for key in old)
report = {"scope": "40 paired seen development questions, same original criteria, no human gold; in-process ASGI real provider, serial runs not randomized load/SLA",
    "v2": old_metrics, "v3": new_metrics,
    "semantic_gains": [key for key in old if not old_semantics[key] and new_semantics[key]],
    "semantic_regressions": [key for key in old if old_semantics[key] and not new_semantics[key]],
    "latency_or_cost_significance_claim": False, "default_promoted": False,
    "conclusion": "Grouping repaired training background, but introduced two semantic regressions and one route degradation. Focus4/4 did not generalize to full40; do not choose a best subset or treat 40 repeated questions as unseen evidence."}
(HERE / "full40-comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
