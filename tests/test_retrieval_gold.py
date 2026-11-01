"""知识点级 retrieval gold 数据集的离线完整性测试。"""
import json
import os
from collections import Counter

import pytest

import config
from tests.eval_retrieval_gold import GOLD_PATH, matches_evidence, validate_gold
from tests.train_learning_ranker import SPLIT_PATH, load_split

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

    gold = json.load(open(GOLD_PATH, encoding="utf-8"))
    train, dev = load_split(SPLIT_PATH, gold)

    assert len(train) == 60
    assert len(dev) == 20
    assert set(case["id"] for case in train).isdisjoint(case["id"] for case in dev)
    assert Counter(case["primary_domain"] for case in train) == {
        domain: 6 for domain in Counter(case["primary_domain"] for case in gold)
    }
    assert Counter(case["primary_domain"] for case in dev) == {
        domain: 2 for domain in Counter(case["primary_domain"] for case in gold)
    }

    from scripts.evaluate_rag_development import GOLD, SPLIT, read, select_cases

    gold, split = read(GOLD), read(SPLIT)
    selected = select_cases(gold, split)
    assert len(selected) == 20
    assert list(Counter(case["primary_domain"] for case in selected).values()) == [2] * 10
    ids = {case["id"] for case in selected}
    assert ids <= set(split["train"])
    assert ids.isdisjoint(split["dev"])
    assert selected == select_cases(list(reversed(gold)), split)


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
