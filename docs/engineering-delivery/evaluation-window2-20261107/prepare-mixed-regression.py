"""Reuse only the previously approved four fragments; all private artifacts local."""

import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent


def read(p):
    return json.loads(p.read_text(encoding="utf8"))


def write(p, v):
    p.write_text(json.dumps(v, ensure_ascii=False, indent=2) + "\n", encoding="utf8")


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


old = ROOT / "data/review_packets/nist-mixed-private-20261104"
source = ROOT / "evaluation/nist-public-http-cards-v3r2-module-20261107"
packet = ROOT / "data/review_packets/nist-mixed-cards-regression-20261107"
assert (
    sha(old / "outgoing-preview.json")
    == read(old / "approval-scope.json")["outgoing_preview_sha256"]
)
import config  # noqa: E402
from retrieval.hybrid import HybridRetriever  # noqa: E402
from retrieval.bm25 import BM25Index  # noqa: E402
from storage import cache  # noqa: E402

for flag in (
    "ENABLE_DENSE_RETRIEVAL",
    "ENABLE_RERANKING",
    "ENABLE_QUERY_VARIANTS",
    "ENABLE_FEATURE_RERANKING",
    "ENABLE_LEARNED_RANKING",
):
    setattr(config, flag, False)
config.RETRIEVER_K = 2
cache._client, cache._live = cache._MemClient(), False
chunks = read(old / "chunks.json")
retriever = HybridRetriever.__new__(HybridRetriever)
retriever._bm25 = BM25Index.build(chunks)
retriever._chunks_by_id = {c["id"]: c for c in chunks}
retriever._dense_degraded = False
preview = read(old / "outgoing-preview.json")
proof = []
for c, expected in zip(read(old / "cases.json"), preview, strict=True):
    contexts = retriever.retrieve(c["question"], top_k=2)
    # Exact original text/identity proof, not only preview prefix equivalence.
    assert c["id"] == expected["id"] and c["question"] == expected["question"]
    assert [
        {key: x.get(key) for key in ("source", "page", "locator", "text")} for x in contexts
    ] == [
        {key: x.get(key) for key in ("source", "page", "locator", "text")}
        for x in expected["contexts"]
    ]
    assert [x["text"] for x in contexts] == [x["text"] for x in expected["contexts"]]
    proof.append(
        {
            "id": c["id"],
            "same_approved_top2_full_text": True,
            "context_text_sha256": [
                hashlib.sha256(x["text"].encode()).hexdigest() for x in contexts
            ],
        }
    )
statefile = HERE / "continuation.json"
state = read(statefile)
b = state["budget"]
assert not b["pending_allocations"] and b["accounted_with_reservations_usd"] + 0.08 <= 3
assert all(sha(ROOT / name) == h for name, h in read(source / "runtime-source-hashes.json").items())
packet.mkdir(exist_ok=False)
for name in ("chunks.json",):
    shutil.copyfile(old / name, packet / name)
for name in (
    "card-selector.json",
    "contract.py",
    "prompts.json",
    "runtime-source-hashes.json",
    "source-units.json",
    "translations.json",
    "translation-review.json",
):
    shutil.copyfile(source / name, packet / name)
cases = read(old / "cases.json")
for c in cases:
    c["expected_card_path"] = False
write(packet / "cases.json", cases)
write(
    packet / "scope-proof.json",
    {
        "original_approval_sha256": sha(old / "approval-scope.json"),
        "original_preview_sha256": sha(old / "outgoing-preview.json"),
        "rows": proof,
        "scope": "Same four previously approved full private fragments, no new course material; exact original texts and questions offline verified. Original8call cap not reused, new8 calls allocated inside standing3USD.",
    },
)
write(
    packet / "funding-preflight.json",
    {
        "prior_accounted_usd": b["accounted_with_reservations_usd"],
        "unknown_reservations": b["unknown_fee_reservations"],
        "max_calls": 8,
        "allocation_usd": 0.08,
        "max_accounted_usd": b["accounted_with_reservations_usd"] + 0.08,
        "total_authorization_usd": 3,
    },
)
m = read(source / "manifest.json")
m.update(
    max_calls=8,
    budget_usd=0.08,
    original_cases_sha256=sha(old / "cases.json"),
    role="Same approved private4 actual mixed app regression; unknown card sources must fallback to original answer, no new private fragments.",
    acceptance="Same4 original criteria, exact approvedTop2 texts, durableSSE/SQL/providerreceipt binding, fallback trace, no new disclosure. AI development only.",
)
m["frozen_sha256"] = {p.name: sha(p) for p in packet.iterdir() if p.is_file()}
write(packet / "manifest.json", m)
write(
    packet / "authorization.json",
    {"max_calls": 8, "budget_usd": 0.08, "manifest_sha256": sha(packet / "manifest.json")},
)
state["active_experiment"] = {
    "packet": packet.relative_to(ROOT).as_posix(),
    "output": "data/evaluation/nist-mixed-cards-regression-20261107-live",
    "status": "scope_verified_frozen",
    "max_calls": 8,
    "budget_usd": 0.08,
}
state["pending_batches"] = [state["active_experiment"]]
b["pending_allocations"] = [{"batch": packet.name, "max_calls": 8, "budget_usd": 0.08}]
write(statefile, state)
controller = (HERE / "dispatch-http32-module.py").read_text(encoding="utf8")
controller = controller.replace(
    "evaluation/nist-public-http-cards-v3r2-module-20261107",
    "data/review_packets/nist-mixed-cards-regression-20261107",
)
controller = controller.replace(
    "docs/engineering-delivery/nist-public-http-cards-v3r2-module-20261107-live",
    "data/evaluation/nist-mixed-cards-regression-20261107-live",
)
controller = (
    controller.replace("64", "8")
    .replace(".20", ".08")
    .replace("http32-module-console.log", "mixed-cards-console.log")
)
(HERE / "dispatch-mixed-regression.py").write_text(controller, encoding="utf8")
write(
    HERE / "mixed-scope-summary.json",
    {
        "cases": 4,
        "private_fragments": 4,
        "exact_original_scope_verified": True,
        "scope_proof_sha256": sha(packet / "scope-proof.json"),
        "max_calls": 8,
        "budget_usd": 0.08,
        "packet_and_results_local_only": True,
        "new_private_fragments": 0,
    },
)
print(
    json.dumps(
        {
            "approved_same_private_fragments": 4,
            "max_calls": 8,
            "max_accounted_usd": b["accounted_with_reservations_usd"] + 0.08,
        }
    )
)
