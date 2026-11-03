"""Offline replay; needs the hash-matched local course corpus. No model calls."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from retrieval.bm25 import BM25Index  # noqa: E402

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def replay():
    frozen = read(HERE / "manifest.json")
    for name, expected in frozen["source_sha256"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Local source changed")
    for name, expected in read(HERE / "regression-manifest.json")["frozen_sha256"].items():
        if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen experiment input changed")
    natural_path = ROOT / "evaluation/nist-public-http-natural-zh-v2/cases.json"
    if hashlib.sha256(natural_path.read_bytes()).hexdigest() != frozen["frozen_cases_sha256"]:
        raise ValueError("Frozen natural questions changed")
    natural, regression = read(natural_path), read(HERE / "regression-cases.json")
    if not natural or not regression:
        raise ValueError("Empty evaluation input")
    course = read(ROOT / "data/kb_chunks.json")
    baseline = read(ROOT / "evaluation/nist-public-http-natural-zh-v2/chunks.json")
    candidate = read(HERE / "candidate-chunks.json")
    if {c["id"]: c["text"] for c in baseline} != {c["id"]: c["text"] for c in candidate}:
        raise ValueError("Primary source text changed")
    counts = {}
    for arm, chunks in (("baseline", baseline), ("candidate", candidate)):
        index = BM25Index.build(course + chunks)

        def rank(case, index=index):
            return next((i + 1 for i, hit in enumerate(index.search(case["question"], 5))
                         if hit.get("locator") == case["target_locator"]), None)

        natural_ranks, regression_ranks = [rank(c) for c in natural], [rank(c) for c in regression]
        negative_hits = [[h.get("locator") for h in index.search(q, 5) if h.get("control_id")]
                         for q in read(HERE / "negative-cases.json")]
        counts[arm] = {"natural_ranks": natural_ranks, "regression_ranks": regression_ranks,
                       "negative_hits": negative_hits}
    before, after = counts["baseline"], counts["candidate"]
    counts["passed"] = (all(rank is not None for rank in after["natural_ranks"])
                        and all(not hits for hits in after["negative_hits"])
                        and all(old is None or new is not None for old, new in
                                zip(before["regression_ranks"], after["regression_ranks"], strict=True)))
    return counts


if __name__ == "__main__":
    result = replay()
    print(json.dumps(result))
    if not result["passed"]:
        raise SystemExit(1)
