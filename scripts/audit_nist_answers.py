"""Audit frozen fixed-context answer runs; semantic acceptance needs source review."""
import argparse
import hashlib
import json
from pathlib import Path

from scripts.answer_grade_contract import materialize_grade
from scripts.audit_flash_answers import citation_checks


def audit(packet, run):
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    manifest = read(packet / "manifest.json")
    for name, digest in manifest["frozen_sha256"].items():
        if hashlib.sha256((packet / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Frozen input changed")
    cases = read(packet / "cases.json")
    regression = read(packet / "at2-regression.json")
    expected = [{"id": "at2-regression", "question": regression["question"],
                 "fixed_contexts": regression["contexts"]}] + cases
    if len({case["id"] for case in expected}) != len(expected):
        raise ValueError("Duplicate input IDs")
    raw = read(run / "results.json")
    results = {row["id"]: row for row in raw}
    if len(results) != len(raw) or set(results) - {case["id"] for case in expected}:
        raise ValueError("Duplicate or unknown result IDs")
    checks = []
    for case in expected:
        check = {"id": case["id"], "status": "not_completed"}
        if case["id"] in results:
            row = results[case["id"]]
            contexts = [{k: c[k] for k in ("source", "locator", "source_url", "text")}
                        for c in case["fixed_contexts"]]
            if row["question"] != case["question"] or row["contexts"] != contexts:
                raise ValueError("Result does not match frozen question/context")
            check["citation_structure"] = citation_checks(row["answer"], row["contexts"])
            check["answer_chars"] = len(row["answer"])
            try:
                check["grade"] = materialize_grade(json.loads(row["judge"]), row,
                                                  allow_absent_source=manifest.get("allow_absent_source", False))
                check["status"] = "validated"
            except (ValueError, KeyError, TypeError) as exc:
                check.update(status="invalid_grade", error_type=type(exc).__name__)
        checks.append(check)
    return {"checks": checks, "semantic_acceptance": "requires_separate_source_review",
            "independent_human_gold": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("packet", "run", "output"):
        parser.add_argument("--" + option, type=Path, required=True)
    args = parser.parse_args()
    value = audit(args.packet, args.run)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
