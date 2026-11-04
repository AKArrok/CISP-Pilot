"""Missing, stale, or duplicate annotations cannot produce a passing summary."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.summarize_http_quality import source_review_summary, summarize


def fixture():
    cases = [{"id": "one"}, {"id": "two"}]
    rows = [{"id": case["id"], "events": [{"type": "token", "content": "private excerpt"}]}
            for case in cases]
    reviews = [{"id": case["id"], "answer_sha256": hashlib.sha256(b"private excerpt").hexdigest(),
                "accepted": True, "reason_code": "supported", "reviewer": "AI source review"}
               for case in cases]
    return cases, rows, reviews


@pytest.mark.parametrize("mutation", ["stale_answer", "duplicate_review", "unknown_review", "string_boolean"])
def test_invalid_review_provenance_fails_closed(mutation):
    cases, rows, reviews = fixture()
    if mutation == "stale_answer":
        rows[0]["events"][0]["content"] += " changed"
    elif mutation == "duplicate_review":
        reviews.append(copy.deepcopy(reviews[0]))
    elif mutation == "unknown_review":
        reviews[0]["id"] = "not-in-results"
    else:
        reviews[0]["accepted"] = "false"
    with pytest.raises(ValueError):
        source_review_summary(cases, rows, reviews)


def test_partial_run_and_negative_decisions_remain_visible_without_private_text():
    cases, rows, reviews = fixture()
    reviews[0]["accepted"] = False
    result = source_review_summary(cases, rows[:1], reviews[:1])
    assert result["planned"] == 2
    assert result["accepted"] == 0
    assert result["missing_ids"] == ["two"]
    assert result["failed_ids"] == ["one"]
    assert not result["all_reviewed_and_accepted"]
    assert "private excerpt" not in json.dumps(result)


def test_empty_reviews_cannot_pass_by_vacuous_truth():
    cases, rows, _reviews = fixture()
    assert not source_review_summary(cases, rows, [])["all_reviewed_and_accepted"]
    with pytest.raises(ValueError):
        source_review_summary([], [], [])


def test_saved_semantic_failure_is_not_hidden_by_structural_success():
    root = Path(__file__).resolve().parents[1]
    result = summarize(root / "evaluation/nist-public-http-citationfix-regression-v1",
                       root / "docs/engineering-delivery/nist-public-http-citationfix-regression-20261104-live")
    assert result["structural_passed"]
    assert result["source_review"]["accepted"] == 17
    assert result["source_review"]["failed_ids"] == ["at-2-new-02-repeat2"]
    assert not result["development_checks_passed"]
    assert not result["automatic_production_acceptance"]


def test_saved_partial_batch_cannot_be_reported_as_completed():
    root = Path(__file__).resolve().parents[1]
    result = summarize(root / "evaluation/nist-public-http-full32-focused-v2",
                       root / "docs/engineering-delivery/nist-public-http-full32-focused-20261104-r2-live")
    assert result["source_review"]["planned"] == 32
    assert result["source_review"]["recorded"] == 20
    assert len(result["source_review"]["missing_ids"]) == 12
    assert not result["execution_complete"]
    assert not result["development_checks_passed"]
