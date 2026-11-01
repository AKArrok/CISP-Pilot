from copy import deepcopy

import pytest

from scripts.eval_answer_workflow import packet, prompts, validate_grade


def test_deductions_require_real_answer_and_source_quotes():
    item = packet("offline")[0]
    value = {"scores": {"completeness": 2, "source_support": 1, "citation_support": 2},
             "reason": "test", "deductions": [{"dimension": "source_support", "reason": "test",
             "answer_quote": item["answer"][:3], "source_ref": 1, "source_quote": "none_found"}]}
    assert validate_grade(value, item) == value
    invalid = deepcopy(value)
    invalid["deductions"][0]["source_quote"] = "invented source passage"
    with pytest.raises(ValueError, match="source quote"):
        validate_grade(invalid, item)
    invalid = deepcopy(value)
    invalid["deductions"] = []
    with pytest.raises(ValueError, match="lacks evidence"):
        validate_grade(invalid, item)
    invalid = deepcopy(value)
    invalid["scores"]["source_support"] = True
    with pytest.raises(ValueError, match="integers"):
        validate_grade(invalid, item)


def test_calibration_labels_are_not_sent_to_judge():
    items = packet("offline")
    assert len(items) == 16 and len({x["category"] for x in items}) == 7
    for item in items:
        payload = prompts(item, {})
        assert "expected_source_support" not in payload[1]["content"]
        assert item["contexts"][0]["text"] in __import__('json').loads(payload[1]["content"])["data"]["contexts"][0]["text"]


def test_risk_checks_cannot_bypass_literal_quote_validation():
    item = packet("offline")[0]
    value = {"scores": {"completeness": 2, "source_support": 2, "citation_support": 2},
             "reason": "test", "deductions": [], "risk_checks": [{
                 "answer_quote": item["answer"][:3], "source_quote": "invented source",
                 "verdict": "supported"}]}
    with pytest.raises(ValueError, match="risk source quote"):
        validate_grade(value, item)
    value["risk_checks"][0].update(source_quote="none_found", verdict="unsupported")
    assert validate_grade(value, item) == value
