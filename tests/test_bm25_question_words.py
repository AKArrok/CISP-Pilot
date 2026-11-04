"""Logical question words must not outweigh the actual training topic."""
import json
from pathlib import Path

from retrieval.bm25 import BM25Index


def test_training_score_question_retrieves_training_before_generic_requirements():
    root = Path(__file__).resolve().parents[1]
    chunks = json.loads((root / "evaluation/nist-public-http-full32-final-v1/chunks.json").read_text(encoding="utf-8"))
    cases = json.loads((root / "evaluation/nist-public-http-full32-final-v1/cases.json").read_text(encoding="utf-8"))
    question = next(case["question"] for case in cases if case["id"] == "at-2-new-08")
    hits = BM25Index.build(chunks).search(question, 101)
    parents = list(dict.fromkeys(hit["locator"] for hit in hits))
    assert "at-2" in parents[:2]
