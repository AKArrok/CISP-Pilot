"""embedding 磁盘缓存：命中不发起请求，key 随 model+dim 区分。"""
import json

import pytest

import config
from llms import DashScopeEmbeddings


class _FakeResponse:
    def __init__(self, vectors):
        self.data = [type("Item", (), {"embedding": v})() for v in vectors]


@pytest.fixture
def embedder(tmp_path, monkeypatch):
    cache_path = str(tmp_path / "embedding_cache.json")
    monkeypatch.setattr(config, "EMBEDDING_CACHE_PATH", cache_path)
    monkeypatch.setattr(config, "EMBEDDING_CACHE_ENABLED", True)
    emb = DashScopeEmbeddings(api_key="test-key", base_url="https://example.invalid",
                              model="test-embedding", dimension=4)
    calls = []

    def fake_create(batch):
        calls.append(list(batch))
        return _FakeResponse([[float(len(batch)), 0.0, float(len(calls)), 1.0]] * len(batch))

    monkeypatch.setattr(emb, "_create_embeddings", fake_create)
    return emb, calls, cache_path


def test_second_call_served_from_cache(embedder):
    emb, calls, _ = embedder

    first = emb.embed_documents(["你好", "世界"])
    second = emb.embed_documents(["你好", "世界"])

    assert len(calls) == 1  # 第二次全部命中缓存，不发请求
    assert first == second == [[2.0, 0.0, 1.0, 1.0], [2.0, 0.0, 1.0, 1.0]]


def test_mixed_hit_and_miss_keeps_order(embedder):
    emb, calls, _ = embedder
    emb.embed_documents(["已知"])

    mixed = emb.embed_documents(["已知", "新文本", "已知"])

    assert len(calls) == 2  # 只对 1 条新文本再请求
    assert calls[1] == ["新文本"]
    assert mixed == [[1.0, 0.0, 1.0, 1.0], [1.0, 0.0, 2.0, 1.0], [1.0, 0.0, 1.0, 1.0]]


def test_cache_persists_across_instances(embedder):
    emb, calls, cache_path = embedder
    emb.embed_query("这条查询")

    with open(cache_path, encoding="utf-8") as fp:
        assert len(json.load(fp)) == 1

    fresh = DashScopeEmbeddings(api_key="test-key", base_url="https://example.invalid",
                                model="test-embedding", dimension=4)
    monkeypatched = fresh
    monkeypatched._create_embeddings = lambda batch: (_ for _ in ()).throw(
        AssertionError("缓存命中时不允许发起请求"))
    vector = monkeypatched.embed_query("这条查询")

    assert vector == [1.0, 0.0, 1.0, 1.0]
    assert len(calls) == 1


def test_cache_key_differs_by_model(embedder):
    emb, calls, _ = embedder
    emb.embed_query("同一文本")

    other_model = DashScopeEmbeddings(api_key="test-key", base_url="https://example.invalid",
                                      model="other-embedding", dimension=4)
    other_calls = []
    monkeypatched = other_model
    monkeypatched._create_embeddings = lambda batch: (
        other_calls.append(list(batch)) or _FakeResponse([[9.0, 9.0, 9.0, 9.0]] * len(batch)))
    vector = monkeypatched.embed_query("同一文本")

    assert other_calls  # 换模型必须缓存未命中重新请求
    assert vector == [9.0, 9.0, 9.0, 9.0]
    assert len(calls) == 1

def test_transient_replace_lock_is_retried(embedder, monkeypatch):
    import os
    import llms

    emb, _, cache_path = embedder
    real_replace = os.replace
    attempts = []

    def flaky_replace(source, destination):
        attempts.append((source, destination))
        if len(attempts) < 3:
            raise PermissionError("simulated Windows file lock")
        real_replace(source, destination)

    monkeypatch.setattr(llms.os, "replace", flaky_replace)

    emb.embed_query("需要重试的缓存")

    assert len(attempts) == 3
    with open(cache_path, encoding="utf-8") as fp:
        assert len(json.load(fp)) == 1
