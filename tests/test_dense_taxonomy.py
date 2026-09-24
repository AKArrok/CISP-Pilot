"""dense 知识点召回和开发集隔离规则。"""
import json
from pathlib import Path

import pytest


class _FakeEmbedder:
    def embed_documents(self, texts):
        vectors = []
        for text in texts:
            if "恢复时间目标" in text or "多快恢复业务" in text:
                vectors.append([1.0, 0.0])
            elif "恢复点目标" in text:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([0.1, 0.1])
        return vectors


class _CrossDomainEmbedder:
    def embed_documents(self, texts):
        vectors = []
        for text in texts:
            if "数字签名" in text or "不可否认" in text:
                vectors.append([1.0, 0.0, 0.0])
            elif "VLAN" in text:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return vectors


class _MisleadingEmbedder:
    def embed_documents(self, texts):
        vectors = []
        for text in texts:
            if "目标知识点" in text:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([1.0, 0.0])
        return vectors


def test_dense_retrieval_matches_semantic_paraphrase_within_domain():
    from data_ingest.dense_taxonomy import build_dense_question_tags

    points = [
        {"id": "rto", "domain": "业务连续性", "name": "恢复时间目标", "status": "active"},
        {"id": "rpo", "domain": "业务连续性", "name": "恢复点目标", "status": "active"},
        {"id": "other", "domain": "信息安全管理", "name": "恢复时间目标", "status": "active"},
    ]
    questions = [{
        "id": "q1", "domain": "业务连续性", "stem": "灾难后要求多快恢复业务？",
        "options": {}, "answer": "", "analysis": "", "needs_review": False,
    }]
    tags = build_dense_question_tags(
        points, questions, _FakeEmbedder(), min_similarity=0.5, min_margin=0.1,
    )
    assert [tag["knowledge_point_id"] for tag in tags] == ["rto"]


def test_dense_domain_inference_can_correct_wrong_stored_domain():
    from data_ingest.dense_taxonomy import build_dense_question_tags

    points = [
        {"id": "signature", "domain": "信息安全支撑技术", "name": "数字签名", "status": "active"},
        {"id": "vlan", "domain": "物理与网络通信安全", "name": "VLAN", "status": "active"},
    ]
    questions = [{
        "id": "q1", "domain": "物理与网络通信安全",
        "stem": "哪种技术能够提供不可否认性？",
        "options": {"A": "数字签名", "B": "VLAN"}, "answer": "A",
        "analysis": "", "needs_review": False,
    }]
    tags = build_dense_question_tags(
        points, questions, _CrossDomainEmbedder(), min_similarity=0.5,
        min_margin=0.0, domain_mode="inferred",
    )
    assert tags[0]["knowledge_point_id"] == "signature"
    assert tags[0]["predicted_domain"] == "信息安全支撑技术"


def test_strong_answer_evidence_is_not_lost_outside_dense_top_five():
    from data_ingest.dense_taxonomy import build_dense_question_tags

    points = [
        {"id": f"other-{index}", "domain": "信息安全管理", "name": f"干扰概念{index}",
         "status": "active"}
        for index in range(6)
    ] + [{
        "id": "target", "domain": "信息安全管理", "name": "目标知识点",
        "status": "active",
    }]
    questions = [{
        "id": "q1", "domain": "信息安全管理", "stem": "这道题考查什么？",
        "options": {"A": "目标知识点", "B": "其他"}, "answer": "A",
        "analysis": "", "needs_review": False,
    }]
    tags = build_dense_question_tags(
        points, questions, _MisleadingEmbedder(), min_similarity=0.5, min_margin=0.0,
    )
    assert tags[0]["knowledge_point_id"] == "target"


def test_dense_domain_mode_rejects_unknown_value():
    from data_ingest.dense_taxonomy import build_dense_question_tags

    try:
        build_dense_question_tags([], [], _FakeEmbedder(), domain_mode="unknown")
    except ValueError as exc:
        assert "domain_mode" in str(exc)
    else:
        raise AssertionError("unknown domain mode must fail")


@pytest.mark.skipif(
    not Path("data/questions.json").exists(),
    reason="当前 checkout 不含真实题库资产",
)
def test_development_set_is_reproducible_and_disjoint():
    from tests.eval_profile_dev import validate_dev

    cases = json.loads(open("tests/fixtures/profile_accuracy_dev.json", encoding="utf-8").read())
    questions = json.loads(open("data/questions.json", encoding="utf-8").read())
    integrity = validate_dev(cases, questions)
    assert integrity["sample_size"] == 50
    assert integrity["unique"]
    assert integrity["selection_exact"]
    assert integrity["missing"] == []
    assert integrity["overlap"] == []
    assert set(integrity["domain_counts"].values()) == {5}


def test_external_holdout_template_is_blocked_until_human_review():
    from tests.eval_profile_external_holdout import validate_external_holdout

    fixture = json.loads(open(
        "tests/fixtures/profile_accuracy_external_holdout.template.json", encoding="utf-8"
    ).read())
    result = validate_external_holdout(
        fixture, current_questions=[], taxonomy=[],
    )
    assert result["ready"] is False
    assert result["checks"]["two_distinct_blind_reviewers"] is False
    assert result["checks"]["minimum_100_questions"] is False
    assert result["checks"]["review_provenance_bound"] is False
    assert result["checks"]["source_questions_hash_matches"] is False


@pytest.mark.skipif(
    not Path("data/questions.json").exists(),
    reason="当前 checkout 不含真实题库资产",
)
def test_external_holdout_rejects_renamed_current_question():
    from tests.eval_profile_external_holdout import validate_external_holdout

    current = json.loads(open("data/questions.json", encoding="utf-8").read())[0]
    questions = [{
        "id": f"external-{index}", "stem": current["stem"],
        "options": current["options"], "answer": current["answer"],
        "review_labels": {"reviewer-a": ["x"], "reviewer-b": ["x"]},
        "acceptable": ["x"], "adjudicated_domain": "x",
    } for index in range(100)]
    fixture = {
        "schema_version": 2,
        "source": {"kind": "external"},
        "reviewers": [
            {"reviewer_id": "reviewer-a"}, {"reviewer_id": "reviewer-b"},
        ],
        "quality_gates": {}, "questions": questions,
    }
    result = validate_external_holdout(fixture)
    assert result["checks"]["external_ids_only"] is True
    assert result["checks"]["external_content_only"] is False
    assert result["checks"]["no_internal_duplicate_stems"] is False
