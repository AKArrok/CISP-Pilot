"""记忆层单测 — 独立临时向量文件与缓存，不碰真实数据。"""
import sys

import pytest

sys.path.insert(0, ".")


def _fresh_memory(tmp_path, monkeypatch):
    """指向临时向量文件，并重置缓存。"""
    import config
    import agents.memory  # noqa: F401  确保模块已加载
    monkeypatch.setattr(config, "MEMORY_VECTORS_PATH", str(tmp_path / "memory_vectors.npz"))
    from storage import cache
    monkeypatch.setattr(cache, "_mem_store", {}, raising=False)
    monkeypatch.setattr(cache, "_client", cache._MemClient(), raising=False)
    monkeypatch.setattr(cache, "_live", False, raising=False)
    return sys.modules["agents.memory"]


def test_save_and_load_round(tmp_path, monkeypatch):
    mem = _fresh_memory(tmp_path, monkeypatch)
    mem.save_round("t1", "什么是RTO？", "RTO 是恢复时间目标……", "knowledge")
    hist = mem.load_history("t1")
    assert [h["role"] for h in hist] == ["user", "assistant"]
    assert hist[0]["content"] == "什么是RTO？"
    assert hist[1]["intent"] == "knowledge"


def test_threads_order_and_rounds(tmp_path, monkeypatch):
    mem = _fresh_memory(tmp_path, monkeypatch)
    mem.save_round("tA", "q1", "a1", "knowledge")
    mem.save_round("tB", "q2", "a2", "chitchat")
    mem.save_round("tA", "q3", "a3", "knowledge")  # tA 两轮，且最新
    threads = mem.list_threads()
    assert threads[0]["thread_id"] == "tA"
    assert threads[0]["rounds"] == 2
    assert threads[1]["rounds"] == 1


def test_history_limit_keeps_latest(tmp_path, monkeypatch):
    mem = _fresh_memory(tmp_path, monkeypatch)
    for i in range(3):
        mem.save_round("tX", f"q{i}", f"a{i}", "knowledge")
    hist = mem.load_history("tX", limit=2)
    assert len(hist) == 2
    assert hist[0]["content"] == "q2"  # 保留最新两轮，旧的在前


def test_short_context_keeps_latest_rounds(tmp_path, monkeypatch):
    mem = _fresh_memory(tmp_path, monkeypatch)
    for i in range(7):
        mem.save_round("tS", f"q{i}", f"a{i}", "knowledge")
    hist = mem.load_short_context("tS")
    assert len(hist) == 10
    assert hist[0]["content"] == "q2"


def test_search_long_memory_returns_thread_match(tmp_path, monkeypatch):
    mem = _fresh_memory(tmp_path, monkeypatch)
    mem.save_round("t1", "什么是RTO？", "恢复时间目标", "knowledge")
    mem.save_round("t2", "什么是IDS？", "入侵检测系统", "knowledge")
    hits = mem.search_long_memory("RTO", thread_id="t1", top_k=1)
    assert hits
    assert hits[0]["thread_id"] == "t1"


def test_dimension_change_preserves_existing_memory(tmp_path, monkeypatch):
    mem = _fresh_memory(tmp_path, monkeypatch)
    embeddings = iter([
        ([1.0, 0.0, 0.0], "model-v1"),
        ([1.0, 0.0], "model-v2"),
    ])
    monkeypatch.setattr(mem, "_embed_text", lambda _text: next(embeddings))

    mem.save_round("existing-thread", "旧问题", "旧回答", "knowledge")
    with pytest.raises(ValueError, match="维度不一致"):
        mem.save_round("new-thread", "新问题", "新回答", "knowledge")

    history = mem.load_history("existing-thread")
    assert [item["content"] for item in history] == ["旧问题", "旧回答"]
    assert mem.load_history("new-thread") == []
