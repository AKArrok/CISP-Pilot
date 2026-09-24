"""记忆层：短期上下文缓存 + 长期向量记忆。

短期记忆保存最近几轮消息到 storage.cache；Redis 可用时跨进程共享，不可用时
自动降级为进程内存。长期记忆把每轮对话嵌入到独立本地向量文件，元数据保存
thread_id、原文与时间，避免和课件知识库向量混用。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time

import numpy as np

import config
from storage import cache

_lock = threading.RLock()


def _short_key(thread_id: str) -> str:
    return f"ctx:{thread_id}"


def _meta_path() -> str:
    return config.MEMORY_VECTORS_PATH + ".meta.json"


def _round_text(question: str, answer: str, intent: str) -> str:
    return f"意图: {intent}\n用户: {question}\n助手: {answer}"


def _fallback_embed(text: str, dim: int = 256) -> list[float]:
    """无 embedding 配置时的测试/降级向量，保证记忆接口仍可用。"""
    vec = np.zeros(dim, dtype=np.float32)
    for token in text:
        digest = hashlib.md5(token.encode("utf-8")).digest()
        vec[int.from_bytes(digest[:2], "little") % dim] += 1.0
    norm = np.linalg.norm(vec)
    return (vec / (norm + 1e-9)).tolist()


def _embed_text(text: str) -> tuple[list[float], str]:
    try:
        from llms import get_embeddings

        emb = get_embeddings()
        model = getattr(emb, "model_identity", getattr(emb, "model", "embedding"))
        return emb.embed_query(text), model
    except Exception:
        return _fallback_embed(text), "fallback-hash"


def _load_store() -> tuple[np.ndarray, list[dict]]:
    if not os.path.exists(config.MEMORY_VECTORS_PATH):
        return np.zeros((0, 0), dtype=np.float32), []
    vectors = np.load(config.MEMORY_VECTORS_PATH)["vectors"]
    if os.path.exists(_meta_path()):
        with open(_meta_path(), encoding="utf-8") as fp:
            metadata = json.load(fp)
    else:
        metadata = []
    return vectors.astype(np.float32), metadata


def _save_store(vectors: np.ndarray, metadata: list[dict]) -> None:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    np.savez_compressed(config.MEMORY_VECTORS_PATH, vectors=vectors.astype(np.float32))
    with open(_meta_path(), "w", encoding="utf-8") as fp:
        json.dump(metadata, fp, ensure_ascii=False)


def save_short_context(thread_id: str, question: str, answer: str, intent: str) -> None:
    """把最近 N 轮对话写入上下文缓存。"""
    history = load_short_context(thread_id, limit=config.MEMORY_MAX_ROUNDS * 2)
    now = time.time()
    history.extend([
        {"role": "user", "content": question, "created_at": now},
        {"role": "assistant", "content": answer, "intent": intent, "created_at": now},
    ])
    keep = history[-config.MEMORY_MAX_ROUNDS * 2:]
    cache.set_json(_short_key(thread_id), keep, ttl=config.SHORT_MEMORY_TTL)


def load_short_context(thread_id: str, limit: int | None = None) -> list[dict]:
    rows = cache.get_json(_short_key(thread_id)) or []
    if limit:
        rows = rows[-limit:]
    return rows


def save_round(thread_id: str, question: str, answer: str, intent: str) -> None:
    """一轮对话写入短期缓存，并追加到长期向量记忆。"""
    save_short_context(thread_id, question, answer, intent)
    text = _round_text(question, answer, intent)
    vector, model = _embed_text(text)
    now = time.time()
    row = {
        "id": hashlib.md5(f"{thread_id}:{now}:{text}".encode("utf-8")).hexdigest(),
        "thread_id": thread_id,
        "role": "round",
        "content": text,
        "question": question,
        "answer": answer,
        "intent": intent,
        "created_at": now,
        "embedding_model": model,
    }
    with _lock:
        vectors, metadata = _load_store()
        arr = np.asarray(vector, dtype=np.float32).reshape(1, -1)
        if vectors.size == 0:
            vectors = arr
        elif vectors.shape[1] == arr.shape[1]:
            vectors = np.vstack([vectors, arr])
        else:
            raise ValueError(
                "长期记忆向量维度不一致："
                f"existing={vectors.shape[1]}, new={arr.shape[1]}；"
                "已保留原存储，未写入当前轮次"
            )
        metadata.append(row)
        _save_store(vectors, metadata)


def search_long_memory(query: str, thread_id: str | None = None,
                       top_k: int | None = None) -> list[dict]:
    """按语义相似度召回长期对话记忆。"""
    top_k = top_k or config.LONG_MEMORY_TOP_K
    with _lock:
        vectors, metadata = _load_store()
    if vectors.size == 0 or not metadata:
        return []
    q, _model = _embed_text(query)
    qv = np.asarray(q, dtype=np.float32)
    if qv.shape[0] != vectors.shape[1]:
        return []
    matrix = vectors / (np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9)
    qv = qv / (np.linalg.norm(qv) + 1e-9)
    scores = matrix @ qv
    order = np.argsort(-scores)
    results = []
    for idx in order:
        item = metadata[int(idx)]
        if thread_id and item.get("thread_id") != thread_id:
            continue
        results.append({**item, "score": float(scores[int(idx)])})
        if len(results) >= top_k:
            break
    return results


def load_history(thread_id: str, limit: int = 50) -> list[dict]:
    """从长期向量记忆元数据恢复某会话历史消息。"""
    with _lock:
        _vectors, metadata = _load_store()
    rows = [m for m in metadata if m.get("thread_id") == thread_id]
    rows = sorted(rows, key=lambda r: r.get("created_at", 0))
    history = []
    for r in rows:
        history.append({
            "role": "user",
            "content": r.get("question", ""),
            "intent": None,
            "created_at": r.get("created_at"),
        })
        history.append({
            "role": "assistant",
            "content": r.get("answer", ""),
            "intent": r.get("intent"),
            "created_at": r.get("created_at"),
        })
    return history[-limit:]


def list_threads(limit: int = 20) -> list[dict]:
    """历史会话列表（按最近长期记忆时间倒序）。"""
    with _lock:
        _vectors, metadata = _load_store()
    grouped: dict[str, dict] = {}
    for row in metadata:
        tid = row.get("thread_id")
        if not tid:
            continue
        item = grouped.setdefault(tid, {"thread_id": tid, "last_at": 0.0, "rounds": 0})
        item["last_at"] = max(item["last_at"], row.get("created_at", 0.0))
        item["rounds"] += 1
    return sorted(grouped.values(), key=lambda x: -x["last_at"])[:limit]
