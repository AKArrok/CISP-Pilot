import hashlib
import json
from pathlib import Path

import pytest

from scripts.audit_nist_answers import audit


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "evaluation/nist-answer-quality-v2-baseline"
RUN = ROOT / "docs/engineering-delivery/nist-answer-quality-20261102-baseline-live"


def test_saved_answer_run_retains_review_boundary():
    result = audit(PACKET, RUN)
    assert len(result["checks"]) == 13
    assert all(row["status"] == "validated" for row in result["checks"])
    assert result["semantic_acceptance"] == "requires_separate_source_review"
    assert result["independent_human_gold"] is False


@pytest.mark.parametrize("mutation", ["context", "question", "duplicate"])
def test_result_cannot_substitute_frozen_inputs(tmp_path, mutation):
    rows = json.loads((RUN / "results.json").read_text(encoding="utf-8"))
    if mutation == "context":
        rows[0]["contexts"][0]["text"] += " injected source"
    elif mutation == "question":
        rows[0]["question"] = "different question"
    else:
        rows.append(rows[0])
    (tmp_path / "results.json").write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ValueError):
        audit(PACKET, tmp_path)


def test_dynamic_retrieval_is_recomputed_from_frozen_corpus(tmp_path):
    packet, run = tmp_path / "packet", tmp_path / "run"
    packet.mkdir()
    run.mkdir()
    source = {"source": "NIST", "source_url": "https://example.invalid/frozen", "locator": "x-1",
              "text": "alpha account control"}
    other = {**source, "locator": "y-1", "text": "beta unrelated"}
    chunks = [{**source, "control_id": "x-1", "embedding_text": "alpha account"},
              {**other, "control_id": "y-1", "embedding_text": "beta"}]
    values = {"cases.json": [{"id": "dynamic", "question": "alpha", "target_control": "x-1"}],
              "chunks.json": chunks,
              "at2-regression.json": {"question": "regression", "contexts": [source]}}
    for name, value in values.items():
        (packet / name).write_text(json.dumps(value), encoding="utf-8")
    manifest = {"retrieval_arm": "bilingual", "frozen_sha256": {
        name: hashlib.sha256((packet / name).read_bytes()).hexdigest() for name in values}}
    (packet / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    grade = json.dumps({"scores": {"completeness": 2, "source_support": 2, "citation_support": 2},
                        "reason": "supported", "deductions": []})
    rows = [{"id": identity, "question": question, "contexts": [source],
             "answer": "alpha [资料1]《NIST》定位【x-1】", "judge": grade}
            for identity, question in (("at2-regression", "regression"), ("dynamic", "alpha"))]
    (run / "results.json").write_text(json.dumps(rows), encoding="utf-8")
    assert all(row["status"] == "validated" for row in audit(packet, run)["checks"])
    rows[1]["contexts"] = [other]
    (run / "results.json").write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen question/context"):
        audit(packet, run)
