"""Expose all cited literal units for every failed native-ownership answer."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


compiled = read(HERE / "literal-source-units.json")
sources = {row["canonical_text_sha256"]: row for row in compiled["sources"]}
summary = read(HERE / "ownership-ablation-summary.json")
failures = []
for batch in summary["records"]:
    directory = ROOT / batch["batch"]
    rows = {row["id"]: row for row in read(directory / "results.json")}
    for failure in batch["failures"]:
        row = rows[failure["id"]]
        answer = json.loads(row["answer_structured"])
        evidence = []
        for reference in answer["source_lines"]:
            original = row["contexts"][reference["source_id"] - 1]
            digest = hashlib.sha256(original["text"].encode()).hexdigest()
            source = sources[digest]
            units = [unit for unit in source["units"] if unit["parent_line"] == reference["line"]]
            assert units and "".join(unit["text"] for unit in units) == original["text"].splitlines()[reference["line"] - 1]
            evidence.append({"source_id": reference["source_id"], "parent_line": reference["line"],
                "literal_unit_count": len(units), "literal_units": units,
                "native_parameter_bindings": source["parameter_bindings"]})
        failures.append({"batch": batch["batch"], "id": row["id"], "question": row["question"],
            "answer_sha256": hashlib.sha256(row["answer_structured"].encode()).hexdigest(),
            "answer": answer["answer"], "existing_AI_failure_reason": failure["reason"],
            "cited_literal_units": evidence})
assert len(failures) == summary["candidate_observations"] - summary["candidate_supported_AI"]
result = {"scope": "All previously failed native-ownership candidates; no new labels, model calls or semantic verdicts",
    "failure_count": len(failures), "all_failures_retained": True, "failures": failures,
    "interpretation": "Multi-unit parent-line citation can be literal yet leave scope unresolved. Units preserve context and are not independently asserted facts.",
    "next": "Prototype fine-unit references with parent context; original semantic and repeat/negative/full32/mixed4 gates remain."}
target = HERE / "evidence-breadth-diagnostic.json"
if target.exists():
    raise FileExistsError("Preserve prior diagnosis")
target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"failed_candidates": len(failures), "max_units_per_cited_parent_line": max(
    item["literal_unit_count"] for row in failures for item in row["cited_literal_units"])}))
