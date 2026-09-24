"""混合检索：dense（向量）+ sparse（BM25）→ RRF 融合 → top-k。

进程内单例：首次调用时加载向量库与 BM25 索引。
"""
from __future__ import annotations

import json
import logging
import os
import threading

import config
from retrieval.query_rewrite import rewrite_query


def _rrf_fuse(dense: list[dict], sparse: list[dict], k: int = 60) -> list[dict]:
    """Reciprocal Rank Fusion，按 chunk id 去重合并。"""
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}
    for rank, item in enumerate(dense):
        cid = item["id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
        items[cid] = item
    for rank, item in enumerate(sparse):
        cid = item["id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
        items.setdefault(cid, item)
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    return [{**items[cid], "rrf_score": s} for cid, s in ranked]


def _merge_pools(pool_lists: list[list[dict]], original_weight: float = 2.0,
                 k: int = 60) -> list[dict]:
    """多查询候选池 RRF 融合：首列表（原查询）权重加倍，其余变体权重 1。"""
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}
    for list_index, pool in enumerate(pool_lists):
        weight = original_weight if list_index == 0 else 1.0
        for rank, item in enumerate(pool):
            cid = item["id"]
            scores[cid] = scores.get(cid, 0.0) + weight / (k + rank + 1)
            items.setdefault(cid, item)
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    return [{**items[cid], "score": s} for cid, s in ranked]


class HybridRetriever:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        from data_ingest.manifest import validate_index
        from retrieval.vector_store import VectorStore
        from retrieval.bm25 import BM25Index

        validate_index()  # 启动自检：数据资产齐全且与清单一致
        if not os.path.exists(config.VECTORS_PATH):
            raise FileNotFoundError(
                f"向量索引不存在: {config.VECTORS_PATH}，请先运行 python data_ingest/build_index.py"
            )
        self._vector = VectorStore.load()
        # JSON 是向量索引的规范数据源，必须保留父子切片字段并与向量 ID 严格对齐。
        with open(config.KB_CHUNKS_PATH, encoding="utf-8") as fp:
            chunks = json.load(fp)
        self._bm25 = BM25Index.build(chunks)
        self._chunks_by_id = {c["id"]: c for c in chunks}
        # dense 召回不可用（embedding 额度耗尽/网络故障）时置位，进程内走 BM25 兜底。
        self._dense_degraded = False
        logging.info("  检索层就绪: %d 向量块 / %d BM25 文档",
                     self._vector._matrix.shape[0], len(self._chunks_by_id))

    @classmethod
    def get(cls) -> "HybridRetriever":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def retrieve(self, query: str, top_k: int | None = None,
                 domain: str | None = None,
                 context_pages: list[tuple[str, int]] | None = None,
                 query_variants: list[str] | None = None) -> list[dict]:
        """返回 top-k 知识块（含 id/domain/source/page/text/score）。

        domain 指定时在该知识域内检索（刷题"关联课件"用）。
        context_pages 为多轮追问时上一轮命中的 (source, page) 列表，
        特征重排会对知识邻域页小幅加成（单轮传 None，加成不生效）。
        query_variants 为 RAG-Fusion 式检索变体（完整问句版/关键词纠错版），
        各路检索后 RRF 融合（原查询权重加倍）；无变体时走单查询原语义。
        """
        top_k = top_k or config.RETRIEVER_K
        retrieval_query = rewrite_query(query) if config.ENABLE_QUERY_REWRITE else query
        variants = [v for v in (query_variants or [])
                    if v.strip() and v.strip() != retrieval_query][:config.QUERY_VARIANT_MAX] \
            if config.ENABLE_QUERY_VARIANTS else []
        # 检索结果 Redis 缓存（同一查询/域过滤组合在 TTL 内直接复用）
        import hashlib
        ctx_fp = hashlib.md5(
            json.dumps(sorted(context_pages or []), ensure_ascii=False).encode()
        ).hexdigest()[:8] if context_pages else "none"
        var_fp = hashlib.md5(json.dumps(variants, ensure_ascii=False).encode()).hexdigest()[:8]
        cache_payload = (
            f"{query}|{retrieval_query}|{top_k}|{domain}|ctx{ctx_fp}|var{var_fp}|"
            f"dense{int(config.ENABLE_DENSE_RETRIEVAL and not self._dense_degraded)}|"
            f"d{config.DENSE_K}|s{config.SPARSE_K}|rrf{config.RRF_K}|"
            f"feature{config.ENABLE_FEATURE_RERANKING}|"
            f"learned{config.ENABLE_LEARNED_RANKING}:{config.LEARNED_RANKER_QUERY_TYPES}:{config.LEARNED_RANKER_PATH}|"
            f"rerank{config.ENABLE_RERANKING}:{config.RERANK_BACKEND}:{config.RERANK_TOP_K}"
        )
        cache_key = f"ret:{hashlib.md5(cache_payload.encode()).hexdigest()}"
        from storage import cache
        cached = cache.get_json(cache_key)
        if cached is not None:
            return cached
        # 逐查询跑召回核心；多变体时 RRF 融合（原查询权重加倍），
        # 单查询路径不做融合，保持既有打分语义零扰动。
        degraded_at_start = self._dense_degraded
        pool_lists = [self._core_retrieval(
            retrieval_query,
            domain,
            not config.ENABLE_DENSE_RETRIEVAL or self._dense_degraded,
        )]
        for variant in variants:
            pool_lists.append(self._core_retrieval(
                variant,
                domain,
                not config.ENABLE_DENSE_RETRIEVAL or self._dense_degraded,
            ))
        full = pool_lists[0] if len(pool_lists) == 1 else _merge_pools(pool_lists, k=config.RRF_K)
        # 同一 Parent 的多个 Child 只保留召回排名最高者，避免重复上下文挤占 top-k。
        unique_parents = []
        seen_parents = set()
        for chunk in full:
            parent_id = chunk.get("parent_id", chunk["id"])
            if parent_id not in seen_parents:
                seen_parents.add(parent_id)
                unique_parents.append(chunk)
        full = unique_parents

        if config.ENABLE_FEATURE_RERANKING:
            from retrieval.rank_features import fold_near_duplicate_pages, rerank_with_features
            full = fold_near_duplicate_pages(
                rerank_with_features(retrieval_query, full, context_pages))

        if config.ENABLE_LEARNED_RANKING:
            from retrieval.learning_ranker import (
                load_model,
                rerank_with_learned_model,
                should_apply_learned_ranker,
            )
            if should_apply_learned_ranker(retrieval_query, config.LEARNED_RANKER_QUERY_TYPES):
                model = load_model(config.LEARNED_RANKER_PATH)
                full = rerank_with_learned_model(retrieval_query, full, model)

        # 精排：用 Child 检索文本评分，最终返回完整 Parent 文本。
        if config.ENABLE_RERANKING:
            from retrieval.reranker import rerank
            full = rerank(query, full[:config.RERANK_TOP_K], top_k)
        results = full[:top_k]
        # 首次 dense 故障发生在 cache key 计算之后；不要把 BM25 结果写进 dense key。
        if self._dense_degraded == degraded_at_start:
            cache.set_json(cache_key, results, ttl=config.RETRIEVAL_CACHE_TTL)
        return results

    def _core_retrieval(self, retrieval_query: str, domain: str | None,
                        degraded: bool) -> list[dict]:
        """单查询召回核心：dense+BM25 → RRF → 补全元数据 → 域过滤/兜底。"""
        from llms import get_embeddings

        query_vec = None
        if not degraded:
            try:
                query_vec = get_embeddings().embed_query(retrieval_query)
            except Exception:
                if not config.ALLOW_DENSE_DEGRADATION:
                    raise
                self._dense_degraded = True
                logging.exception("dense 召回不可用，本进程降级为 BM25 兜底召回（%s 条）",
                                  config.DENSE_DEGRADED_SPARSE_K)
        if query_vec is not None:
            dense = self._vector.search(query_vec, config.DENSE_K)
            dense_scores = {item["id"]: item["score"] for item in dense}
        else:
            dense = []
            dense_scores = {}
        sparse_k = config.SPARSE_K if query_vec is not None else config.DENSE_DEGRADED_SPARSE_K
        sparse = self._bm25.search(retrieval_query, sparse_k)
        fused = _rrf_fuse(dense, sparse, k=config.RRF_K)
        # 统一补全 chunk 元数据（向量库 metadata 只含 id）；
        # dense_score 供回答层做相关性门限判断，不参与排序。
        full = []
        for c in fused:
            chunk = self._chunks_by_id[c["id"]]
            full.append({
                **chunk,
                "score": c.get("rrf_score", c.get("score", 0.0)),
                "dense_score": dense_scores.get(c["id"]),
            })
        if domain:
            full = [c for c in full if c["domain"] == domain]
            # 域内过滤后不足时，用 BM25 域内结果补
            if len(full) < config.RETRIEVER_K:
                seen = {c["id"] for c in full}
                extra = [c for c in self._bm25.search(retrieval_query, config.SPARSE_K * 2)
                         if c["domain"] == domain and c["id"] not in seen]
                full.extend(extra[:config.RETRIEVER_K - len(full)])
        return full


if __name__ == "__main__":
    import sys
    retriever = HybridRetriever.get()
    for q in sys.argv[1:] or ["什么是深度防御"]:
        print(f"\n== {q}")
        for r in retriever.retrieve(q, top_k=3):
            print(f"  [{r['domain']}|{r['source']} p{r['page']}] {r['text'][:80]}...")
