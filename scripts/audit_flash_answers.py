"""Local structural audit of the 36 frozen answers; semantic scores stay unknown."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agents.sources import source_location  # noqa: E402

INPUTS = ROOT / "docs/engineering-delivery/rag-answer-prepared-20261010/answer-inputs.json"
RUNS = ("flash-small-20261028", "flash-candidate-20261028")


def normalize_title(text):
    return re.sub(r"[\s《》〈〉]", "", text)


def citation_checks(answer, contexts):
    citations = re.findall(r"\[资料(\d+)\]《(.*?)》(第\d+页|定位【[A-Za-z][A-Za-z0-9_.:-]*】)", answer)
    errors = []
    for ref, title, location in citations:
        index = int(ref) - 1
        if not 0 <= index < len(contexts):
            errors.append({"code": "unknown_reference", "ref": ref})
            continue
        source = contexts[index]
        try:
            expected = source_location(source)
        except ValueError:
            errors.append({"code": "missing_source_location", "ref": ref})
            continue
        if normalize_title(title) != normalize_title(source["source"]) or location != expected:
            errors.append({"code": "source_page_mismatch" if source.get("page") is not None else "source_locator_mismatch",
                           "ref": ref, "quoted_source": title, "quoted_location": location,
                           "expected_source": source["source"], "expected_location": expected})
    if not citations:
        errors.append({"code": "no_parseable_citation"})
    for ref in set(re.findall(r"\[资料(\d+)\]", answer)):
        if not 1 <= int(ref) <= len(contexts):
            errors.append({"code": "unknown_inline_reference", "ref": ref})
    return {"parsed_citations": len(citations), "errors": errors,
            "semantic_citation_support": "not_scored"}


def audit():
    inputs = json.loads(INPUTS.read_text(encoding="utf-8"))
    contexts = {(x["case_id"], x["arm"]): x["contexts"] for x in inputs}
    hashes = {str(INPUTS.relative_to(ROOT)): hashlib.sha256(INPUTS.read_bytes()).hexdigest()}
    rows = []
    for run in RUNS:
        path = ROOT / "docs/engineering-delivery" / run / "receipts.jsonl"
        hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        receipts = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        expected = {(case, arm, trial) for case in ("continuity_event_levels", "continuity_incident_process", "assurance_ppdr")
                    for arm in ("sparse_without_features", "sparse_with_features") for trial in (1, 2, 3)}
        if len(receipts) != 18 or {(x["case_id"], x["arm"], x["trial"]) for x in receipts} != expected:
            raise ValueError("Incomplete or duplicate receipts")
        for item in receipts:
            key = (item["case_id"], item["arm"])
            answer = item.get("answer") or ""
            # These are review leads, not automatic semantic error labels.
            leads = [line for line in answer.splitlines() if any(term in line for term in
                     ("高频", "常考", "笔误", "方向相反", "方向不同", "根除**：清除", "恢复**：将"))]
            rows.append({"run": run, "case_id": key[0], "arm": key[1], "trial": item["trial"],
                         "execution_status": item["status"], "citation_structure": citation_checks(answer, contexts[key]),
                         "review_leads": leads, "completeness": "not_scored", "source_support": "not_scored"})
    return {"dataset_role": "development", "reviewer_type": "programmatic_structure_only",
            "input_sha256": hashes, "rows": rows,
            "summary": {"answers": len(rows), "answers_with_citation_structure_errors": sum(bool(r["citation_structure"]["errors"]) for r in rows),
                        "error_codes": dict(Counter(e["code"] for r in rows for e in r["citation_structure"]["errors"]))}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit()
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result["summary"]))


if __name__ == "__main__":
    main()
