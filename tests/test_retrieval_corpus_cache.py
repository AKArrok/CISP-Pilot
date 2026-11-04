"""Shared cache must not return a prior corpus's text or citation location."""
from types import SimpleNamespace

import pytest

import config
from retrieval.bm25 import BM25Index
from retrieval.hybrid import HybridRetriever
from storage import cache


@pytest.fixture
def isolated_cache(monkeypatch):
    for flag in ("ENABLE_QUERY_REWRITE", "ENABLE_QUERY_VARIANTS", "ENABLE_DENSE_RETRIEVAL",
                 "ENABLE_FEATURE_RERANKING", "ENABLE_LEARNED_RANKING", "ENABLE_RERANKING"):
        monkeypatch.setattr(config, flag, False)
    stored = {}
    monkeypatch.setattr(cache, "get_json", stored.get)
    monkeypatch.setattr(cache, "set_json", lambda key, value, **kw: stored.update({key: value}))
    return stored


def retriever(chunk):
    instance = HybridRetriever.__new__(HybridRetriever)
    instance._dense_degraded = False
    instance._chunks_by_id = {chunk["id"]: chunk}
    instance._bm25 = BM25Index.build([chunk])
    return instance


@pytest.mark.parametrize("change", [{"text": "account new source content"}, {"page": 2}])
def test_new_corpus_does_not_reuse_old_text_or_citation(isolated_cache, change):
    original = {"id": "same-id", "source": "synthetic", "page": 1, "domain": "synthetic",
                "text": "account old source content"}
    first = retriever(original).retrieve("account", top_k=1)
    changed = {**original, **change}
    second = retriever(changed).retrieve("account", top_k=1)
    assert first[0]["text"] == original["text"] and first[0]["page"] == 1
    assert second[0]["text"] == changed["text"] and second[0]["page"] == changed["page"]
    assert len(isolated_cache) == 2


def test_same_corpus_reuses_cache_across_instances(isolated_cache):
    chunk = {"id": "same-id", "source": "synthetic", "page": 1, "domain": "synthetic", "text": "account"}
    expected = retriever(chunk).retrieve("account", top_k=1)
    equivalent = retriever(dict(reversed(list(chunk.items()))))

    def unexpected_search(*args):
        raise AssertionError("Equivalent corpus should hit the shared cache")

    equivalent._bm25 = SimpleNamespace(search=unexpected_search)
    assert equivalent.retrieve("account", top_k=1) == expected
    assert len(isolated_cache) == 1
