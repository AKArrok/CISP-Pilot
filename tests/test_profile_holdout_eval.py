"""独立 holdout 的防泄漏和数据完整性回归。"""
import json
from pathlib import Path

import pytest


CASES = Path("tests/fixtures/profile_accuracy_holdout.json")
CURRENT_QUESTIONS_PRESENT = Path("data/questions.json").exists()
CURRENT_TAG_ARTIFACTS = all(Path(path).exists() for path in (
    "data/knowledge_taxonomy.json",
    "data/question_knowledge_tags.json",
))


@pytest.mark.skipif(
    not CURRENT_QUESTIONS_PRESENT,
    reason="当前 checkout 不含真实题库资产",
)
def test_holdout_is_stratified_clean_and_disjoint():
    from tests.eval_profile_holdout import validate_holdout

    cases = json.loads(CASES.read_text("utf-8"))
    integrity = validate_holdout(cases)
    assert len(cases["knowledge_tag_cases"]) == 100
    assert integrity["unique_ids"]
    assert integrity["frozen_cases_unchanged"]
    assert integrity["stratified"]
    assert integrity["missing_question_ids"] == []
    assert integrity["review_or_duplicate_ids"] == []
    assert integrity["calibration_overlap"] == []
    assert integrity["manual_override_overlap"] == []


@pytest.mark.skipif(
    not CURRENT_TAG_ARTIFACTS,
    reason="当前 checkout 不含真实知识点与题目标签资产",
)
def test_holdout_scores_current_artifacts_without_fixture_predictions():
    from tests.eval_profile_accuracy import evaluate_knowledge_tags

    cases = json.loads(CASES.read_text("utf-8"))
    result = evaluate_knowledge_tags(cases)
    assert result["sample_size"] == 100
    assert result["scorable"] == 100
    assert result["manual_reviewed_cases"] == 0
    assert result["missing_current_predictions"] == []
