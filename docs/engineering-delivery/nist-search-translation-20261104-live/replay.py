"""Offline replay of frozen translated search assets with the application retriever."""
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
from retrieval.bm25 import BM25Index  # noqa: E402
from retrieval.hybrid import HybridRetriever  # noqa: E402
from storage import cache  # noqa: E402

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def replay():
    manifest = read(HERE / "replay-manifest.json")
    for name, expected in manifest["frozen_sha256"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen corpus or experiment input changed")
    for name, expected in manifest["code_sha256"].items():
        if hashlib.sha256((ROOT / name).read_text(encoding="utf-8").encode()).hexdigest() != expected:
            raise ValueError("Retrieval or replay code changed")
    prior = ROOT / "docs/engineering-delivery/nist-large-pool-20261104-offline"
    course = read(ROOT / "data/kb_chunks.json")
    original = read(ROOT / "evaluation/nist-public-http-natural-zh-v2/chunks.json")
    originals = {c["control_id"]: c for c in original}
    natural = read(ROOT / "evaluation/nist-public-http-natural-zh-v2/cases.json")
    old, negatives = read(prior / "regression-cases.json"), read(prior / "negative-cases.json")
    if not natural or not old or not negatives:
        raise ValueError("Empty evaluation input")
    flags = {name: False for name in ("ENABLE_DENSE_RETRIEVAL", "ENABLE_RERANKING",
                                      "ENABLE_FEATURE_RERANKING", "ENABLE_LEARNED_RANKING",
                                      "ENABLE_QUERY_VARIANTS")}
    flags.update(manifest["retrieval_config"])
    results = {}
    with patch.multiple(config, **flags), patch.object(cache, "get_json", return_value=None), \
         patch.object(cache, "set_json", return_value=None), \
         patch("llms.get_embeddings", side_effect=AssertionError("No provider in offline replay")):
        for arm, source in manifest["arms"].items():
            extra = read(ROOT / source)
            if len({c["id"] for c in extra}) != len(extra):
                raise ValueError("Duplicate search chunk ID")
            for chunk in extra:
                parent = originals[chunk["control_id"]]
                if any(chunk.get(key) != parent.get(key) for key in ("text", "locator", "source_url", "parameters")):
                    raise ValueError("Primary source or parameter definition changed")
            corpus = course + extra
            retriever = HybridRetriever.__new__(HybridRetriever)
            retriever._bm25 = BM25Index.build(corpus)
            retriever._chunks_by_id = {c["id"]: c for c in corpus}
            retriever._dense_degraded = False

            def rank(case, retriever=retriever):
                return next((i + 1 for i, hit in enumerate(retriever.retrieve(case["question"], 5))
                             if hit.get("locator") == case["target_locator"]), None)

            results[arm] = {"natural_ranks": [rank(c) for c in natural], "old_ranks": [rank(c) for c in old],
                            "negative_hits": [[h["locator"] for h in retriever.retrieve(q, 5)
                                               if h.get("control_id")] for q in negatives]}
    baseline, final = results["summary"], results["multiscale"]
    results["passed"] = (all(rank is not None for rank in final["natural_ranks"])
                         and all(not hits for hits in final["negative_hits"])
                         and all(before is None or after is not None for before, after in
                                 zip(baseline["old_ranks"], final["old_ranks"], strict=True)))
    return results


if __name__ == "__main__":
    result = replay()
    print(json.dumps(result))
    if not result["passed"]:
        raise SystemExit(1)
