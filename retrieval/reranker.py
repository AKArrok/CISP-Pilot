"""精排：对混合检索的候选做 query 相关性重排序。

后端由 RERANK_BACKEND 决定：
- "dashscope"：qwen3.7-text-rerank API（独立计费额度，延迟约百毫秒级）；
- "local"：bge-reranker-v2-m3 交叉编码器（CPU 上 10-20 个候选约数秒，
  调用方应放在线程池，hybrid 检索已被 to_thread 包裹）。

任何后端失败都优雅降级：跳过精排，保持 RRF 顺序。
"""
from __future__ import annotations

import logging
import threading

import config
from agents.tracing import observe

_lock = threading.Lock()
_predict_gate = threading.Semaphore(1)  # CPU 重排序串行化，避免并发互相拖垮
_cross_encoder = None
_failed = False


def _candidate_text(chunk: dict) -> str:
    return chunk.get("embedding_text") or chunk.get("child_text") or chunk["text"]


def _get_encoder():
    global _cross_encoder, _failed
    if _failed:
        return None
    if _cross_encoder is None:
        with _lock:
            if _cross_encoder is None and not _failed:
                try:
                    from sentence_transformers import CrossEncoder
                    logging.info("加载精排模型 %s ...", config.LOCAL_RERANKER_MODEL)
                    _cross_encoder = CrossEncoder(config.LOCAL_RERANKER_MODEL, max_length=512)
                except Exception:
                    logging.exception("精排模型加载失败，已降级为 RRF 顺序（不影响可用性）")
                    _failed = True
                    return None
    return _cross_encoder


def _rerank_remote(query: str, candidates: list[dict]) -> list[float]:
    """返回与 candidates 顺序对齐的精排分（不在这里排序，融合需要原始特征序）。"""
    from llms import get_reranker

    return [float(s) for s in get_reranker().rerank(query, [_candidate_text(c) for c in candidates])]


def _rerank_local(query: str, candidates: list[dict]) -> list[float] | None:
    encoder = _get_encoder()
    if encoder is None:
        return None
    pairs = [[query, _candidate_text(c)] for c in candidates]
    with _predict_gate:
        from agents.budget import current_budget
        if current_budget.get() is not None:
            current_budget.get().reserve("local_rerank")
        scores = encoder.predict(pairs, show_progress_bar=False)
    return [float(s) for s in scores]


def _fuse_order(scores: list[float], n: int, alpha: float) -> list[int]:
    """特征序（输入列表顺序）与精排分的加权融合：α·精排分 + (1-α)·特征名次。

    两侧各做集合内归一：精排分 min-max，特征名次线性。平局保持特征序。
    α=1 退化为纯精排序，α=0 退化为纯特征序。
    """
    r_min, r_max = min(scores), max(scores)
    span = (r_max - r_min) or 1.0
    fused = []
    for i, score in enumerate(scores):
        r_norm = (score - r_min) / span
        f_norm = (n - i) / max(n - 1, 1)
        fused.append(-(alpha * r_norm + (1 - alpha) * f_norm))
    return sorted(range(n), key=lambda i: (fused[i], i))


def rerank(query: str, candidates: list[dict], top_k: int) -> list[dict]:
    """对候选拿 query 相关性重排序，返回 top-k（失败时原序截断）。

    输入列表已是特征重排后的顺序；RERANK_FUSION=alpha 时按
    RERANK_FUSION_ALPHA 与特征名次加权融合，replace 为纯精排序。
    """
    if not candidates:
        observe("rerank_backend", "skipped_no_candidates")
        return []
    if len(candidates) == 1:
        observe("rerank_backend", "skipped_single_candidate")
        return candidates[:top_k]
    try:
        if config.RERANK_BACKEND == "dashscope":
            scores = _rerank_remote(query, candidates)
        else:
            scores = _rerank_local(query, candidates)
    except Exception:
        observe("rerank_backend", "failed_degraded")
        logging.exception("精排失败，降级为 RRF 顺序")
        return candidates[:top_k]
    if scores is None or len(scores) != len(candidates):
        observe("rerank_backend", "unavailable_or_invalid_scores_degraded")
        return candidates[:top_k]
    observe("rerank_backend", "scores_received")
    if config.RERANK_FUSION == "alpha":
        order = _fuse_order(scores, len(scores), config.RERANK_FUSION_ALPHA)
    else:
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    return [{**candidates[i], "rerank_score": scores[i]} for i in order][:top_k]
