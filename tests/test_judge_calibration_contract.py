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


def test_source_score_cannot_be_full_when_review_identifies_unsupported_claim():
    item = {"answer": "A is required.", "contexts": [{"text": "A is discussed."}]}
    grade = {"scores": {"completeness": 2, "source_support": 2, "citation_support": 2},
             "reason": "test", "deductions": [], "risk_checks": [{"answer_quote": item["answer"],
             "source_quote": "none_found", "verdict": "unsupported", "dimension": "source_support"}]}
    with pytest.raises(ValueError, match="risk verdict conflicts"):
        validate_grade(grade, item)
    grade["risk_checks"][0]["dimension"] = "citation_support"
    assert validate_grade(grade, item) == grade
    grade["risk_checks"][0]["dimension"] = "source_support"
    grade["scores"]["source_support"] = 1
    grade["deductions"] = [{"dimension": "source_support", "reason": "unsupported claim",
                            "answer_quote": item["answer"], "source_quote": "none_found"}]
    assert validate_grade(grade, item) == grade


def test_source_score_cannot_be_full_when_review_identifies_contradiction():
    item = {"answer": "A is required.", "contexts": [{"text": "A is prohibited."}]}
    grade = {"scores": {"completeness": 2, "source_support": 2, "citation_support": 2},
             "reason": "test", "deductions": [], "risk_checks": [{"answer_quote": item["answer"],
             "source_quote": item["contexts"][0]["text"], "verdict": "contradicted",
             "dimension": "source_support"}]}
    with pytest.raises(ValueError, match="risk verdict conflicts"):
        validate_grade(grade, item)
    grade["risk_checks"][0]["dimension"] = "citation_support"
    assert validate_grade(grade, item) == grade


def test_critic_ensemble_preserves_frozen_baseline_and_can_veto_source_score(tmp_path):
    packet = ROOT / "evaluation/judge-calibration-v5"
    run = ROOT / "docs/engineering-delivery/judge-calibration-20261101-r4"
    assert audit(packet, run)["arm_matches"]["judge_candidate"] == 6
    rows = json.loads((run / "results.json").read_text(encoding="utf-8"))
    row = next(x for x in rows if x["id"] == "scope_not_frequency")
    grade = json.loads(row["judge_candidate"])
    grade["scores"]["source_support"] = 1
    grade["deductions"] = [{"dimension": "source_support", "reason": "synthetic veto",
                            "answer_line": 1, "source_ref": 1, "source_line": 2}]
    row["judge_candidate"] = json.dumps(grade)
    (tmp_path / "results.json").write_text(json.dumps(rows), encoding="utf-8")
    assert audit(packet, tmp_path)["arm_matches"]["judge_candidate"] == 7


def test_duplicate_or_unknown_results_cannot_be_silently_overwritten(tmp_path):
    packet = ROOT / "evaluation/judge-calibration-v5"
    original = ROOT / "docs/engineering-delivery/judge-calibration-20261101-r5/results.json"
    rows = json.loads(original.read_text(encoding="utf-8"))
    (tmp_path / "results.json").write_text(json.dumps(rows + [rows[0]]), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate or unknown"):
        audit(packet, tmp_path)
    rows[0]["id"] = "unknown"
    (tmp_path / "results.json").write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate or unknown"):
        audit(packet, tmp_path)


def test_absent_source_is_allowed_only_in_explicit_new_protocol():
    item = {"answer": "未回答。", "contexts": [{"text": "原文"}]}
    grade = {"scores": {"completeness": 0, "source_support": 2, "citation_support": 2},
             "reason": "未回答", "deductions": [{"dimension": "completeness", "reason": "未回答",
             "answer_line": 1, "source_ref": None, "source_line": None}]}
    with pytest.raises(ValueError, match="source reference"):
        materialize_grade(grade, item)
    assert materialize_grade(grade, item, allow_absent_source=True)["deductions"][0]["source_quote"] == "none_found"
    grade["deductions"][0]["source_line"] = 1
    with pytest.raises(ValueError, match="source reference"):
        materialize_grade(grade, item, allow_absent_source=True)


def test_absent_source_cannot_support_a_claim():
    item = {"answer": "A", "contexts": [{"text": "B"}]}
    grade = {"scores": {"completeness": 2, "source_support": 2, "citation_support": 2},
             "reason": "test", "deductions": [], "risk_checks": [{"answer_line": 1,
             "source_ref": None, "source_line": None, "verdict": "supported"}]}
    with pytest.raises(ValueError):
        materialize_grade(grade, item, allow_absent_source=True)


def test_historical_v5_remains_five_of_eight():
    result = audit(ROOT / "evaluation/judge-calibration-v5",
                   ROOT / "docs/engineering-delivery/judge-calibration-20261101-r5")
    assert result["arm_matches"]["judge_candidate"] == 5
    assert result["passed"] is False


def test_opted_in_null_source_protocol_also_applies_to_frozen_primary():
    result = audit(ROOT / "evaluation/judge-calibration-scope-v3",
                   ROOT / "docs/engineering-delivery/judge-calibration-scope-20261102-live")
    empty = next(row for row in result["checks"] if row["id"] == "empty_answer")
    assert empty["status"] == "validated"
    assert empty["score"] == 0
    assert result["arm_matches"]["judge_candidate"] == 10


def test_empty_calibration_cannot_pass(tmp_path):
    packet = tmp_path / "packet"
    packet.mkdir()
    (packet / "cases.json").write_text("[]", encoding="utf-8")
    (packet / "manifest.json").write_text('{"judge_arms": ["judge_candidate"]}', encoding="utf-8")
    (tmp_path / "results.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="requires cases and judge arms"):
        audit(packet, tmp_path)

    (packet / "cases.json").write_text('[{"id": "one"}]', encoding="utf-8")
    (packet / "manifest.json").write_text('{"judge_arms": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="requires cases and judge arms"):
        audit(packet, tmp_path)


def test_audit_rejects_changed_frozen_cases(tmp_path):
    packet = tmp_path / "packet"
    packet.mkdir()
    (packet / "cases.json").write_text("[]", encoding="utf-8")
    (packet / "manifest.json").write_text('{"frozen_sha256": {"cases.json": "wrong"}}', encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen input changed"):
        audit(packet, tmp_path)
