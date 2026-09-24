"""精排后端与 dense 降级兜底的单元测试。"""
import os

import pytest

import config
import retrieval.reranker as reranker_module
from retrieval.reranker import rerank
from llms import DashScopeReranker


def test_dashscope_reranker_maps_scores_by_index():
    payload = {"output": {"results": [
        {"index": 2, "relevance_score": 0.9},
        {"index": 0, "relevance_score": 0.5},
    ]}}

    scores = DashScopeReranker._map_results(payload, 4)

    assert scores == [0.5, 0.0, 0.9, 0.0]


def test_remote_rerank_orders_candidates_by_score(monkeypatch):
    class StubReranker:
        def rerank(self, query, documents, top_n=None):
            assert len(documents) == 2
            return [0.1, 0.9]

    import llms
    monkeypatch.setattr(llms, "get_reranker", lambda: StubReranker())
    monkeypatch.setattr(config, "RERANK_FUSION", "replace")  # 后端语义：纯精排序
    candidates = [
        {"id": "weak", "text": "弱相关"},
        {"id": "strong", "text": "强相关"},
    ]

    ranked = rerank("测试查询", candidates, top_k=2)

    assert [c["id"] for c in ranked] == ["strong", "weak"]
    assert ranked[0]["rerank_score"] == pytest.approx(0.9)


def test_rerank_degrades_to_original_order_on_error(monkeypatch):
    import llms

    def boom():
        raise RuntimeError("API 不可用")

    monkeypatch.setattr(llms, "get_reranker", boom)
    candidates = [{"id": "a", "text": "甲"}, {"id": "b", "text": "乙"}]

    ranked = rerank("测试查询", candidates, top_k=2)

    assert [c["id"] for c in ranked] == ["a", "b"]


def test_rerank_backend_dispatch(monkeypatch):
    calls = []

    def fake_remote(query, candidates):
        calls.append("remote")
        return [0.1] * len(candidates)

    def fake_local(query, candidates):
        calls.append("local")
        return [0.1] * len(candidates)

    monkeypatch.setattr(reranker_module, "_rerank_remote", fake_remote)
    monkeypatch.setattr(reranker_module, "_rerank_local", fake_local)

    monkeypatch.setattr(config, "RERANK_BACKEND", "dashscope")
    rerank("q", [{"id": "a", "text": "甲"}, {"id": "b", "text": "乙"}], 1)
    monkeypatch.setattr(config, "RERANK_BACKEND", "local")
    rerank("q", [{"id": "a", "text": "甲"}, {"id": "b", "text": "乙"}], 1)

    assert calls == ["remote", "local"]


@pytest.mark.skipif(
    not os.path.exists(config.KB_CHUNKS_PATH),
    reason="当前 checkout 不含真实课件切片资产",
)
def test_dense_degradation_falls_back_to_bm25(monkeypatch):
    """embedding 不可用时，检索不应报错，而应走 BM25 兜底并置降级标志。"""
    import json

    import llms
    from retrieval.hybrid import HybridRetriever

    retriever = HybridRetriever.get()
    retriever._dense_degraded = False
    from storage import cache

    cache_writes = []
    monkeypatch.setattr(cache, "get_json", lambda _key: None)
    monkeypatch.setattr(
        cache,
        "set_json",
        lambda *args, **kwargs: cache_writes.append((args, kwargs)),
    )

    def embed_fail(*args, **kwargs):
        raise RuntimeError("Free quota exhausted")

    monkeypatch.setattr(llms, "get_embeddings", lambda: type("E", (), {
        "embed_query": staticmethod(embed_fail)})())

    results = retriever.retrieve("什么是业务连续性计划", top_k=3)

    assert results, "降级后仍应返回 BM25 召回结果"
    assert retriever._dense_degraded is True
    assert cache_writes == []  # BM25 降级结果不得写入先前计算的 dense cache key
    chunks = json.load(open(config.KB_CHUNKS_PATH, encoding="utf-8"))
    by_id = {c["id"]: c for c in chunks}
    for item in results:
        assert item["id"] in by_id


@pytest.mark.skipif(
    not os.path.exists(config.KB_CHUNKS_PATH),
    reason="当前 checkout 不含真实课件切片资产",
)
def test_dense_can_be_intentionally_disabled_without_error(monkeypatch):
    import llms
    from retrieval.hybrid import HybridRetriever

    retriever = HybridRetriever.get()
    retriever._dense_degraded = False
    monkeypatch.setattr(config, "ENABLE_DENSE_RETRIEVAL", False)
    monkeypatch.setattr(
        llms,
        "get_embeddings",
        lambda: (_ for _ in ()).throw(AssertionError("dense 已关闭，不应加载 embedding")),
    )

    results = retriever.retrieve("显式关闭 dense 的测试查询", top_k=3)

    assert results
    assert retriever._dense_degraded is False

def test_alpha_fusion_respects_feature_order_on_small_gaps(monkeypatch):
    """融合序：精排分小幅领先不翻序（要素更全的答案页保住 Top1）。"""
    import llms

    class StubReranker:
        def rerank(self, query, documents, top_n=None):
            return [0.50, 0.90, 0.00]  # rival 精排分领先但差距不大

    monkeypatch.setattr(llms, "get_reranker", lambda: StubReranker())
    candidates = [
        {"id": "answer", "text": "答案页（特征序第1）"},
        {"id": "rival", "text": "相邻概念页（特征序第2）"},
        {"id": "far", "text": "无关页（特征序第3）"},
    ]

    ranked = rerank("测试查询", candidates, top_k=3)
    assert [c["id"] for c in ranked] == ["answer", "rival", "far"]


def test_alpha_fusion_flips_on_large_rerank_gaps(monkeypatch):
    """融合序：精排分大幅领先时才翻序（特征序真排错的场景）。"""
    import llms

    class StubReranker:
        def rerank(self, query, documents, top_n=None):
            return [0.10, 0.95, 0.50]  # rival 精排分大幅领先

    monkeypatch.setattr(llms, "get_reranker", lambda: StubReranker())
    candidates = [
        {"id": "answer", "text": "答案页（特征序第1）"},
        {"id": "rival", "text": "正确页（特征序第2）"},
        {"id": "far", "text": "无关页（特征序第3）"},
    ]

    ranked = rerank("测试查询", candidates, top_k=3)
    assert [c["id"] for c in ranked] == ["rival", "answer", "far"]

def test_dashscope_reranker_turns_http_error_into_runtime_error(monkeypatch):
    import httpx

    remote = DashScopeReranker(
        api_key="test-key",
        base_url="https://example.invalid",
        model="test-rerank",
    )
    remote.max_retries = 0

    def fail_post(*args, **kwargs):
        request = httpx.Request("POST", "https://example.invalid/rerank")
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(remote._client, "post", fail_post)

    with pytest.raises(RuntimeError, match="status=None"):
        remote._post("查询", ["文档"], 1)
