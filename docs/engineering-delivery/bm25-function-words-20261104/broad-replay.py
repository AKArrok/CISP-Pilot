"""Existing180 course evidence cases: query-word ablation, no external calls."""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import config
from retrieval import bm25
from retrieval.hybrid import HybridRetriever
from storage import cache
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))
from eval_retrieval_gold import resolve_relevant_parent_ids  # noqa: E402

HERE = Path(__file__).resolve().parent


def run():
    manifest = json.loads((HERE / "broad-manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["frozen_sha256"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Frozen input changed")
    chunks = json.loads((ROOT / "data/kb_chunks.json").read_text(encoding="utf-8"))
    cases = []
    for name in manifest["cases"]:
        cases.extend(json.loads((ROOT / name).read_text(encoding="utf-8")))
    if not cases or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Expected nonempty disjoint existing cases")
    flags = dict(ENABLE_DENSE_RETRIEVAL=False, ENABLE_RERANKING=False, ENABLE_FEATURE_RERANKING=False, ENABLE_LEARNED_RANKING=False, ENABLE_QUERY_VARIANTS=False, ENABLE_QUERY_REWRITE=True, SPARSE_K=20, DENSE_DEGRADED_SPARSE_K=60, RRF_K=60)
    results = {}
    with patch.multiple(config, **flags), patch.object(cache, "get_json", return_value=None), patch.object(cache, "set_json", return_value=None), patch("llms.get_embeddings", side_effect=AssertionError("No external model")):
        for name, additions in manifest["arms"].items():
            with patch.object(bm25, "_STOPWORDS", set(manifest["original_stopwords"]) | set(additions)):
                retriever = HybridRetriever.__new__(HybridRetriever)
                retriever._bm25 = bm25.BM25Index.build(chunks)
                retriever._chunks_by_id = {c["id"]: c for c in chunks}
                retriever._dense_degraded = False
                results[name] = []
                for case in cases:
                    relevant = resolve_relevant_parent_ids(chunks, case)
                    if not relevant:
                        raise ValueError("Existing evidence no longer resolves")
                    hits = retriever.retrieve(case["question"], 5)
                    rank = next((i + 1 for i, hit in enumerate(hits) if hit.get("parent_id", hit["id"]) in relevant), None)
                    results[name].append({"id": case["id"], "rank": rank})
    base = {row["id"]: row["rank"] for row in results["baseline"]}
    candidate = {row["id"]: row["rank"] for row in results["combined"]}
    summary = {"cases": len(cases), "baseline_top5": sum(rank is not None for rank in base.values()), "candidate_top5": sum(rank is not None for rank in candidate.values()), "lost_ids": [key for key in base if base[key] is not None and candidate[key] is None], "gained_ids": [key for key in base if base[key] is None and candidate[key] is not None], "independent_human_gold": False, "provider_calls": 0}
    return {"summary": summary, "results": results}


if __name__ == "__main__":
    report = run()
    with (HERE / "broad-results.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report["summary"]))
