"""Offline actual retriever vs removing its existing parent fold. Never invokes a model."""
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

import config  # noqa: E402
from agents.sources import source_preview  # noqa: E402
from retrieval.bm25 import BM25Index  # noqa: E402
from retrieval.hybrid import HybridRetriever  # noqa: E402
from retrieval.query_rewrite import rewrite_query  # noqa: E402
from storage import cache  # noqa: E402


def key(row):
    return (row["source"], row.get("page"), row.get("locator"), hashlib.sha256(row["text"].encode()).hexdigest())


def inspect(chunks, cases):
    for name in ("ENABLE_DENSE_RETRIEVAL", "ENABLE_RERANKING", "ENABLE_FEATURE_RERANKING",
                 "ENABLE_LEARNED_RANKING", "ENABLE_QUERY_VARIANTS"):
        setattr(config, name, False)
    config.ENABLE_QUERY_REWRITE = True
    config.RETRIEVER_K = 2
    cache._client, cache._live = cache._MemClient(), False
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever._chunks_by_id = {chunk["id"]: chunk for chunk in chunks}
    retriever._bm25 = BM25Index.build(chunks)
    retriever._dense_degraded = True
    rows = []
    for case in cases:
        query = case["question"]
        raw = retriever._core_retrieval(rewrite_query(query), None, True)
        actual = retriever.retrieve(query, top_k=2)
        without_fold = raw[:2]
        rows.append({"id": case["id"], "raw_pool": len(raw), "raw_pool_unique_parents": len({r.get("parent_id", r["id"]) for r in raw}),
                     "without_fold_unique_evidence": len({key(r) for r in without_fold}),
                     "actual_unique_evidence": len({key(r) for r in actual}), "actual_k": len(actual),
                     "target_hit": None if not case.get("target_locator") else case["target_locator"] in [r.get("locator") for r in actual],
                     "actual_previews": [source_preview(row) for row in actual]})
    return rows


def main():
    packet = ROOT / "evaluation/concise-40-baseline-20261110"
    chunks = json.loads((packet / "chunks.json").read_text(encoding="utf-8"))
    cases = json.loads((packet / "cases.json").read_text(encoding="utf-8"))
    public = inspect(chunks, cases)
    groups = {}
    for chunk in chunks:
        groups.setdefault(key(chunk), []).append(chunk)
    result = {"model_calls": 0, "public_corpus_sha256": hashlib.sha256((packet / "chunks.json").read_bytes()).hexdigest(),
              "physical_chunks": len(chunks), "distinct_parent_evidence": len(groups),
              "same_text_groups_share_parent": all(len({c.get("parent_id", c["id"]) for c in group}) == 1 for group in groups.values()),
              "public_cases": public, "public_summary": {"cases": len(public), "target_hits": sum(r["target_hit"] is True for r in public),
                  "duplicate_final_top2": sum(r["actual_unique_evidence"] < r["actual_k"] for r in public),
                  "raw_top2_duplicate_cases_without_existing_fold": sum(r["without_fold_unique_evidence"] < min(2, r["raw_pool"]) for r in public)},
              "scope": "Existing parent fold remains unchanged. Removing it is a diagnostic counterfactual, not a deployed arm. Hash duplicate evidence is not proof of semantic redundancy."}
    private_path = ROOT / "data/kb_chunks.json"
    if private_path.exists():
        private = json.loads(private_path.read_text(encoding="utf-8"))
        queries = ["系统备份与灾难恢复", "业务连续性与恢复目标", "安全意识培训", "系统使用告知", "账号和访问权限管理",
                   "网络访问与身份鉴别", "安全事件响应流程", "审计日志分析", "渗透测试与漏洞扫描", "风险评估与控制措施"]
        observed = inspect(private, [{"id": f"local-probe-{i}", "question": q} for i, q in enumerate(queries, 1)])
        result["local_private_summary"] = {"corpus_sha256": hashlib.sha256(private_path.read_bytes()).hexdigest(),
            "chunks": len(private), "probes": len(observed),
            "duplicate_final_top2": sum(r["actual_unique_evidence"] < r["actual_k"] for r in observed),
            "without_existing_fold_duplicate_top2": sum(r["without_fold_unique_evidence"] < min(2, r["raw_pool"]) for r in observed),
            "raw_private_text_published": False, "semantic_answers_scored": False}
    output = Path(__file__).resolve().parent / "retrieval-diagnostic.json"
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"public": result["public_summary"], "private": result.get("local_private_summary")}))


if __name__ == "__main__":
    main()
