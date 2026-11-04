"""Frozen offline ablation of logical question words; no model or index writes."""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import config
from retrieval import bm25
from retrieval.hybrid import HybridRetriever
from storage import cache

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def run():
    paths = {"course": "data/kb_chunks.json", "nist": "docs/engineering-delivery/nist-search-translation-20261104-live/multiscale-chunks.json", "old": "docs/engineering-delivery/nist-large-pool-20261104-offline/regression-cases.json", "negative": "docs/engineering-delivery/nist-large-pool-20261104-offline/negative-cases.json", "natural": "evaluation/nist-public-http-natural-zh-v2/cases.json", "public32": "evaluation/nist-public-http-full32-final-v1/cases.json"}
    frozen = read(HERE / "manifest.json")
    for name, digest in frozen["frozen_sha256"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Frozen input changed")
    data = {key: read(ROOT / name) for key, name in paths.items()}
    flags = dict(ENABLE_DENSE_RETRIEVAL=False, ENABLE_RERANKING=False, ENABLE_FEATURE_RERANKING=False, ENABLE_LEARNED_RANKING=False, ENABLE_QUERY_VARIANTS=False, ENABLE_QUERY_REWRITE=True, SPARSE_K=20, DENSE_DEGRADED_SPARSE_K=60, RRF_K=60)
    results = {}
    original = set(bm25._STOPWORDS)
    with patch.multiple(config, **flags), patch.object(cache, "get_json", return_value=None), patch.object(cache, "set_json", return_value=None), patch("llms.get_embeddings", side_effect=AssertionError("No external model")):
        for name, additions in frozen["arms"].items():
            with patch.object(bm25, "_STOPWORDS", original | set(additions)):
                result = {}
                for scope, chunks in [("mixed", data["course"] + data["nist"]), ("public", data["nist"])]:
                    retriever = HybridRetriever.__new__(HybridRetriever)
                    retriever._bm25 = bm25.BM25Index.build(chunks)
                    retriever._chunks_by_id = {c["id"]: c for c in chunks}
                    retriever._dense_degraded = False
                    groups = ["old", "natural"] if scope == "mixed" else ["public32"]
                    for group in groups:
                        result[group] = [next((i + 1 for i, hit in enumerate(retriever.retrieve(case["question"], 5)) if hit.get("locator") == case["target_locator"]), None) for case in data[group]]
                    if scope == "mixed":
                        result["negative"] = [[hit["locator"] for hit in retriever.retrieve(q, 5) if hit.get("control_id")] for q in data["negative"]]
                results[name] = result
    base = results["baseline"]
    for result in results.values():
        result["passed"] = (all(rank == 1 for rank in result["natural"]) and all(not hits for hits in result["negative"]) and all(before is None or after is not None for before, after in zip(base["old"], result["old"], strict=True)) and all(rank is not None and rank <= 2 for rank in result["public32"]))
    return results


if __name__ == "__main__":
    results = run()
    with (HERE / "results.json").open("x", encoding="utf-8") as stream:
        json.dump(results, stream, ensure_ascii=False, indent=2)
    for name, result in results.items():
        print(name, result["passed"], "old_top5", sum(rank is not None for rank in result["old"]), "public_top2", sum(rank is not None and rank <= 2 for rank in result["public32"]))
