from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.answer_grade_contract import line_payload, materialize_grade, validate_grade
from scripts.audit_judge_calibration import audit


ROOT = Path(__file__).resolve().parents[1]


def test_risk_quote_must_be_a_contiguous_source_substring():
    item = {"answer": "Frequency is unassigned.", "contexts": [{"text": 'A {"label":"frequency"}, B'}]}
    grade = {"scores": {"completeness": 2, "source_support": 2, "citation_support": 2},
             "reason": "test", "deductions": [], "risk_checks": [{"answer_quote": item["answer"],
             "source_quote": '{"label":"frequency"}', "verdict": "supported"}]}
    assert validate_grade(grade, item) == grade
    invented = deepcopy(grade)
    invented["risk_checks"][0]["source_quote"] = '[{"label":"frequency"}]'
    with pytest.raises(ValueError, match="risk source quote"):
        validate_grade(invented, item)


def test_partial_real_run_cannot_pass_even_when_observed_scores_match():
    result = audit(ROOT / "evaluation/judge-calibration-v2",
                   ROOT / "docs/engineering-delivery/judge-calibration-20261030-r2")
    assert result["passed"] is False
    assert result["arm_matches"]["judge_candidate"] == 3
    assert sum(x["status"] == "not_completed" for x in result["checks"]) == 4
    assert sum(x["status"] == "invalid_evidence_or_schema" for x in result["checks"]) == 1


def test_calibration_cases_did_not_change_between_iterations():
    assert (ROOT / "evaluation/judge-calibration-v1/cases.json").read_bytes() == (
        ROOT / "evaluation/judge-calibration-v2/cases.json").read_bytes()
    cases = json.loads((ROOT / "evaluation/judge-calibration-v2/cases.json").read_text(encoding="utf-8"))
    assert len(cases) == 8


def test_line_ids_preserve_original_markdown_and_json_bytes():
    item = {"question": "Q", "answer": "**Claim**\nOther", "contexts": [
        {"source": "NIST", "locator": "x-1", "text": 'Header\n[{"label": "frequency"}, {"label": "events"}]'}],
        "expected_source_support": 1}
    value = {"scores": {"completeness": 2, "source_support": 1, "citation_support": 2},
             "reason": "test", "deductions": [{"dimension": "source_support", "reason": "test",
             "answer_line": 1, "source_ref": 1, "source_line": 2}]}
    result = materialize_grade(value, item)
    assert result["deductions"][0]["answer_quote"] == "**Claim**"
    assert result["deductions"][0]["source_quote"] == item["contexts"][0]["text"].splitlines()[1]
    assert "expected_source_support" not in line_payload(item)
    assert "answer_line" in value["deductions"][0]  # raw result unchanged
    value["deductions"][0]["source_line"] = True
    with pytest.raises(ValueError, match="source line"):
        materialize_grade(value, item)
