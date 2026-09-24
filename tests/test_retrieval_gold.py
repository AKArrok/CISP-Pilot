"""知识点级 retrieval gold 数据集的离线完整性测试。"""
import json
import os

import pytest

import config
from tests.eval_retrieval_gold import GOLD_PATH, matches_evidence, validate_gold

HOLDOUT_PATH = "tests/fixtures/retrieval_holdout.json"


def _load(path):
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


@pytest.mark.skipif(
    not os.path.exists(config.KB_CHUNKS_PATH),
    reason="当前 checkout 不含真实课件切片资产",
)
def test_gold_has_balanced_domain_coverage_and_resolvable_evidence():
    summary = validate_gold(_load(GOLD_PATH), _load(config.KB_CHUNKS_PATH))

    assert summary["cases"] == 80
    # 10 个知识域各 8 题，防止某个高频领域把整体指标刷高。
    assert set(summary["domains"].values()) == {8}
    assert len(summary["query_types"]) >= 5


@pytest.mark.skipif(
    not os.path.exists(config.KB_CHUNKS_PATH),
    reason="当前 checkout 不含真实课件切片资产",
)
def test_holdout_has_no_question_overlap_and_resolvable_evidence():
    gold = _load(GOLD_PATH)
    holdout = _load(HOLDOUT_PATH)
    summary = validate_gold(holdout, _load(config.KB_CHUNKS_PATH))

    assert summary["cases"] == 100
    assert set(summary["domains"].values()) == {10}
    assert {case["question"] for case in gold}.isdisjoint(case["question"] for case in holdout)


def test_evidence_matching_requires_source_page_and_terms():
    evidence = {"source": "课件", "page": 3, "must_contain": ["RTO", "RPO"]}
    chunk = {"source": "课件", "page": 3, "text": "RTO表示恢复时间，RPO表示恢复点。"}

    assert matches_evidence(chunk, evidence)
    assert not matches_evidence({**chunk, "page": 4}, evidence)
    assert not matches_evidence({**chunk, "text": "只有RTO"}, evidence)
