import hashlib
from pathlib import Path

from scripts.eval_nist_batch import load, retrieve


PACKET = Path(__file__).resolve().parents[1] / "evaluation/nist-development-v1"


def test_frozen_packet_has_32_unique_development_questions_and_exact_sources():
    manifest = load(PACKET / "manifest.json")
    for filename, digest in manifest["frozen_sha256"].items():
        assert hashlib.sha256((PACKET / filename).read_bytes()).hexdigest() == digest
    cases = load(PACKET / "cases.json")
    chunks = load(PACKET / "chunks.json")
    assert len(cases) == len({case["question"] for case in cases}) == 32
    for case in cases:
        assert case["reference_evidence"] == next(c["text"] for c in chunks if c["control_id"] == case["target_control"])


def test_retrieval_comparison_keeps_all_arms_and_cases():
    cases, chunks = load(PACKET / "cases.json"), load(PACKET / "chunks.json")
    rows = retrieve(cases, chunks)
    assert len(rows) == 96
    assert all(r["target_rank"] == 1 for r in rows if r["arm"] == "bilingual")
    assert sum(r["target_rank"] is None for r in rows if r["arm"] == "source_only") == 31


def test_unrelated_query_does_not_gain_supplement_hits():
    rows = retrieve([{"id": "negative", "question": "TCP拥塞窗口慢启动", "target_control": None}], load(PACKET / "chunks.json"))
    assert all(not row["locators"] for row in rows)
