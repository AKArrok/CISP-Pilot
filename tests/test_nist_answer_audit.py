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
