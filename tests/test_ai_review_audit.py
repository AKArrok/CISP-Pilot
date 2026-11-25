from copy import deepcopy

import pytest

from scripts.audit_ai_review import review_outcome


@pytest.mark.parametrize("claim,status", [("unsupported", "failed"), ("uncertain", "unknown")])
def test_pass_cannot_override_problematic_claim(claim, status):
    review = {"verdict": "pass", "issues": [{"status": claim}]}
    original = deepcopy(review)
    assert review_outcome(review) == {"status": status, "inconsistent": True,
                                      "reason": claim + "_claim"}
    assert review == original


def test_fail_with_all_supported_claims_requires_adjudication():
    result = review_outcome({"verdict": "fail", "issues": [{"status": "supported"}]})
    assert result["status"] == "unknown"
    assert result["inconsistent"]


@pytest.mark.parametrize("review", [None, {}, {"verdict": []}, {"verdict": "pass", "issues": [{"status": []}]}, {"verdict": "pass"},
    {"verdict": "pass", "issues": ["supported"]},
    {"verdict": "pass", "issues": [{"status": "other"}]}])
def test_invalid_or_incomplete_reviews_are_errors(review):
    assert review_outcome(review)["status"] == "error"


def test_consistent_reports_keep_their_meaning():
    assert review_outcome({"verdict": "pass", "issues": []})["status"] == "passed"
    assert review_outcome({"verdict": "fail", "issues": [{"status": "unsupported"}]})["status"] == "failed"
    assert review_outcome({"verdict": "uncertain", "issues": []})["status"] == "unknown"
def test_course_scope_can_fail_an_otherwise_supported_answer():
    from scripts.audit_ai_review import course_review_outcome

    base = {"verdict": "pass", "issues": [{"status": "supported"}]}
    assert course_review_outcome(base)["status"] == "error"
    assert course_review_outcome(base | {"scope_status": "pass", "scope_reason": "只答准备事项"})["status"] == "passed"
    assert course_review_outcome(base | {"scope_status": "uncertain", "scope_reason": "指代不明"})["status"] == "unknown"
    result = course_review_outcome(base | {"scope_status": "fail", "scope_reason": "附带了后续检查事项"})
    assert result == {"status": "failed", "inconsistent": True, "reason": "out_of_scope_content"}
