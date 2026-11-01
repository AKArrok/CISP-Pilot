"""画像准确度评测集的结构和关键计算回归。"""
import json
from pathlib import Path

import pytest


CASES = Path("tests/fixtures/profile_accuracy_cases.json")
SYNTHETIC_SCHEMA = Path("tests/fixtures/profile_schema_contract.synthetic.json")
CURRENT_TAG_ARTIFACTS = all(Path(path).exists() for path in (
    "data/knowledge_taxonomy.json",
    "data/question_knowledge_tags.json",
))


def test_profile_schema_fixture_has_synthetic_contract():
    from domains import DOMAINS

    cases = json.loads(SYNTHETIC_SCHEMA.read_text("utf-8"))
    assert cases["attempt_budgets"] == [4, 8, 12, 20]
    assert cases["dataset_role"] == "synthetic_profile_schema_contract"
    assert len(cases["personas"]) >= 4
    assert len(cases["knowledge_tag_cases"]) == 40
    assert cases["quality_gates"]["primary_tag_accuracy_min"] == 0.85
    assert cases["provenance"]["human_reviewed"] is False
    assert cases["provenance"]["quality_evaluation_allowed"] is False
    for persona in cases["personas"]:
        assert persona["id"].startswith("profile-contract-persona-")
        assert set(persona["abilities"]) == set(DOMAINS)
        assert set(persona["abilities"].values()) <= {0.2, 0.8}
    for case in cases["knowledge_tag_cases"]:
        assert case["question_id"].startswith("profile-contract-question-")
        assert case["acceptable"]
        assert all(label.startswith("profile-contract-label-") for label in case["acceptable"])
        assert case["predicted"].startswith("profile-contract-label-")
        assert case["scorable"] is False


def test_profile_accuracy_cli_does_not_fallback_to_synthetic(monkeypatch, tmp_path):
    import sys
    from tests import eval_profile_accuracy

    assert eval_profile_accuracy.DEFAULT_CASES == eval_profile_accuracy.ROOT / CASES
    assert eval_profile_accuracy.DEFAULT_CASES != SYNTHETIC_SCHEMA.resolve()
    missing_cases = tmp_path / "missing-profile-accuracy-cases.json"
    report = tmp_path / "profile-report.json"
    monkeypatch.setattr(eval_profile_accuracy, "DEFAULT_CASES", missing_cases)
    monkeypatch.setattr(sys, "argv", ["eval_profile_accuracy.py", "--output", str(report)])

    def unexpected_evaluation(*args, **kwargs):
        pytest.fail("Missing real cases must fail before evaluation or synthetic fallback")

    monkeypatch.setattr(eval_profile_accuracy, "evaluate_inference", unexpected_evaluation)
    monkeypatch.setattr(eval_profile_accuracy, "evaluate_knowledge_tags", unexpected_evaluation)
    with pytest.raises(FileNotFoundError) as error:
        eval_profile_accuracy.main()
    assert error.value.filename == str(missing_cases)
    assert not report.exists()


def test_profile_prediction_uses_production_thresholds():
    from tests.eval_profile_accuracy import _predict

    weak, weak_probability = _predict(correct=1, attempts=8)
    strong, strong_probability = _predict(correct=7, attempts=8)
    assert weak is True
    assert strong is False
    assert weak_probability > strong_probability


@pytest.mark.skipif(
    not CURRENT_TAG_ARTIFACTS,
    reason="当前 checkout 不含真实知识点与题目标签资产",
)
def test_current_primary_tags_are_present_in_benchmark():
    from tests.eval_profile_accuracy import evaluate_knowledge_tags

    cases = json.loads(CASES.read_text("utf-8"))
    assert cases["attempt_budgets"] == [4, 8, 12, 20]
    assert "校准集回归" in cases["dataset_role"]
    assert len(cases["personas"]) >= 4
    assert len(cases["knowledge_tag_cases"]) == 40
    assert cases["quality_gates"]["primary_tag_accuracy_min"] == 0.85
    result = evaluate_knowledge_tags(cases)
    assert result["missing_current_predictions"] == []
    assert result["primary_tag_accuracy"] >= cases["quality_gates"]["primary_tag_accuracy_min"]
    assert result["manual_reviewed_cases"] == 12
    assert result["parse_contamination_rate"] == 0
    assert result["errors"] == []


def test_manual_tag_review_covers_known_heuristic_failures():
    from data_ingest.tag_review import MANUAL_PRIMARY

    assert len(MANUAL_PRIMARY) == 12
    assert MANUAL_PRIMARY["700题_b2_125"][1] == "ISMS的PDCA模型"


def test_manual_tag_review_replaces_heuristic_and_recounts_points():
    from data_ingest.tag_review import apply_manual_tag_review

    points = [{
        "id": "old-risk", "domain": "信息安全管理", "name": "风险处理",
        "evidence_count": 1, "question_count": 1, "status": "active",
        "recommended": True,
    }]
    tags = [{
        "question_id": "700题_b2_125", "knowledge_point_id": "old-risk",
        "confidence": 0.8, "is_primary": True, "tag_source": "heuristic",
    }]

    updated_points, updated_tags = apply_manual_tag_review(points, tags)
    q125_tags = [tag for tag in updated_tags if tag["question_id"] == "700题_b2_125"]
    q125_point = next(point for point in updated_points if point["name"] == "ISMS的PDCA模型")

    assert len(q125_tags) == 1
    assert q125_tags[0]["tag_source"] == "manual"
    assert q125_tags[0]["knowledge_point_id"] == q125_point["id"]
    assert next(point for point in updated_points if point["id"] == "old-risk")["question_count"] == 0
