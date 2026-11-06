"""Freeze the original32 through the actual app, with opt-in source-card node."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from retrieval.bm25 import BM25Index  # noqa: E402
from scripts.eval_nist_batch import request_content  # noqa: E402
import tiktoken  # noqa: E402


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


baseline = ROOT / "evaluation/nist-public-http-default-baseline-20261106"
renderer = ROOT / "evaluation/nist-unit-cards-native-renderer-v3r2-20261107"
packet = ROOT / "evaluation/nist-public-http-cards-v3r2-20261107"
packet.mkdir(exist_ok=False)
for name in ("cases.json", "chunks.json", "prompts.json"):
    shutil.copyfile(baseline / name, packet / name)
for name in ("source-units.json", "translations.json", "translation-review.json"):
    shutil.copyfile(renderer / name, packet / name)
write(packet / "card-selector.json", {"evidence_select_system": read(renderer / "prompts.json")["evidence_select_system"]})
shutil.copyfile(ROOT / "scripts/answer_unit_contract.py", packet / "contract.py")
dependencies = ["agents/graph.py", "agents/prompts.py", "agents/sources.py", "llms.py", "server.py",
    "scripts/evaluate_public_http.py", "scripts/experimental_card_node.py", "scripts/eval_nist_batch.py",
    "scripts/answer_unit_contract.py", "scripts/answer_output_contract.py", "scripts/inline_oscal_parameters.py",
    "scripts/answer_grade_contract.py", "scripts/audit_public_http.py", "scripts/audit_card_materialization.py",
    "retrieval/bm25.py", "retrieval/hybrid.py"]
write(packet / "runtime-source-hashes.json", {name: sha(ROOT / name) for name in dependencies})
statefile = Path(__file__).with_name("continuation.json")
state = read(statefile)
b = state["budget"]
assert not b["pending_allocations"] and not state["pending_batches"]
held = b["known_estimate_usd"] + sum(row["additional_reservation_usd"] for row in b["unknown_fee_reservations"])
assert abs(held-b["accounted_with_reservations_usd"]) < 1e-9 and held+.20 <= b["approved_total_usd"]
write(packet / "funding-preflight.json", {"known_estimate_usd": b["known_estimate_usd"], "unknown_reservations": b["unknown_fee_reservations"],
    "prior_accounted_usd": held, "new_allocation_usd": .20, "max_accounted_usd": held+.20, "approved_total_usd": 3,
    "authorization": "standing user3USD; new64calls onlypublicNIST, no private sources or old-lock retry"})
cases, chunks = read(packet / "cases.json"), read(packet / "chunks.json")
assert sha(packet / "cases.json") == sha(baseline / "cases.json") and sha(packet / "chunks.json") == sha(baseline / "chunks.json")
assert len(cases) == len({case["thread_group"] for case in cases}) == 32
index = BM25Index.build(chunks)
compiled = read(packet / "source-units.json")["sources"]
selector = read(packet / "card-selector.json")["evidence_select_system"]
encoding = tiktoken.get_encoding("cl100k_base")
preflight = []
for case in cases:
    contexts = index.search(case["question"], 2)
    payload = json.loads(request_content("evidence_select", {"question": case["question"], "unit_sources": compiled},
        {"contexts": contexts}, "", {"mode": "answer_card_pipeline"}))
    # Match the adapter's exact JSON serialization, not the SDK compact form.
    tokens = len(encoding.encode(selector)) + len(encoding.encode(json.dumps(payload, ensure_ascii=False)))
    assert tokens <= 5500
    preflight.append({"id": case["id"], "global_BM25_locators": [context["locator"] for context in contexts],
        "target_in_top2": case["target_locator"] in [context["locator"] for context in contexts], "selector_tokens": tokens})
write(packet / "input-preflight.json", {"global_BM25": preflight, "target_top2": sum(row["target_in_top2"] for row in preflight),
    "cases": 32, "max_selector_input_tokens": max(row["selector_tokens"] for row in preflight),
    "limit": "Offline globalBM25 only; actual router/domain/retrieval/trace/persistence and semantic correctness require the live app run", "model_calls": 0})
manifest = read(baseline / "manifest.json")
manifest.update(answer_strategy="source_cards", executor_sha256=sha(ROOT / "scripts/evaluate_public_http.py"),
    role="Original32 byte-identical to defaultbaseline; actual app route/retrieval/CSRF/SSE/SQL with opt-in source cards. AI development only; not independenthuman gold.",
    base_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    host_materialization="Pinned contract source, actual original-unit/translation bindings; internal selector JSON hidden, raw receipts retained, SSE must equal pinned rendering and SQL readback hash.")
manifest["frozen_sha256"] = {path.name: sha(path) for path in packet.iterdir() if path.is_file()}
write(packet / "manifest.json", manifest)
write(packet / "authorization.json", {"max_calls": 64, "budget_usd": .20, "manifest_sha256": sha(packet / "manifest.json")})
state["active_experiment"] = {"packet": packet.relative_to(ROOT).as_posix(),
    "output": "docs/engineering-delivery/nist-public-http-cards-v3r2-20261107-live", "status": "frozen_preflight_passed", "max_calls": 64, "budget_usd": .20}
state["pending_batches"] = [state["active_experiment"]]
b["pending_allocations"] = [{"batch": packet.name, "max_calls": 64, "budget_usd": .20}]
write(statefile, state)
print(json.dumps({"cases": 32, "offline_global_target_top2": sum(row["target_in_top2"] for row in preflight),
    "max_selector_input": max(row["selector_tokens"] for row in preflight), "accounted_max": held+.20}))
