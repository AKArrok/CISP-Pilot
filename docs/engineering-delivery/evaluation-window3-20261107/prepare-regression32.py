"""Freeze the original32 through the unchanged question-part protocol."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from agents.sources import source_preview  # noqa: E402
from scripts.answer_unit_contract import question_parts  # noqa: E402
from scripts.eval_nist_batch import request_content  # noqa: E402
import tiktoken  # noqa: E402


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


source = ROOT/"evaluation/nist-public-http-question-parts-v1-20261107"
original = ROOT/"evaluation/nist-public-http-default-baseline-20261106"
prior_run = ROOT/"docs/engineering-delivery/nist-public-http-cards-v3r2-module-20261107-live"
packet = ROOT/"evaluation/nist-public-http-question-parts-regression32-20261107"
statefile = HERE/"continuation.json"
state = read(statefile)
b = state["budget"]
assert not b["pending_allocations"] and not state["pending_batches"]
assert b["accounted_with_reservations_usd"]+.32 <= b["approved_total_usd"] == 3
assert state["question_parts_v1"]["repeat_AI_source_supported"] == 8
assert all(sha(ROOT/name) == digest for name, digest in read(source/"runtime-source-hashes.json").items())
assert sha(original/"chunks.json") == sha(source/"chunks.json")
packet.mkdir(exist_ok=False)
for name in ("chunks.json", "prompts.json", "card-selector.json", "source-units.json", "translations.json", "translation-review.json", "contract.py"):
    shutil.copyfile(source/name, packet/name)
shutil.copyfile(original/"cases.json", packet/"cases.json")
assert len(read(packet/"cases.json")) == 32
write(packet/"acceptance.json", {"scope": "Original32 source/condition/parameter/citation/refusal criteria unchanged; same cases byte-for-byte",
    "inherited_manifest_acceptance": read(original/"manifest.json")["acceptance"],
    "original_cases_sha256": sha(original/"cases.json"),
    "baseline_source_review": "docs/engineering-delivery/nist-public-http-default-baseline-20261106-live/source-reviews.json",
    "semantic_review_required": True, "human_gold": False})
controller = (HERE/"dispatch-question-parts-v1.py").read_text(encoding="utf-8").replace(
    "nist-public-http-question-parts-v1-20261107", packet.name).replace(
    "question-parts-v1-console.log", "question-parts-regression32-console.log").replace("16", "64").replace(".12", ".32")
controller_file = HERE/"dispatch-regression32.py"
controller_file.write_text(controller, encoding="utf-8")
dependencies = read(source/"runtime-source-hashes.json")
dependencies[controller_file.relative_to(ROOT).as_posix()] = "pending"
write(packet/"runtime-source-hashes.json", {name: sha(ROOT/name) for name in dependencies})
selector = read(packet/"card-selector.json")["evidence_select_system"]
encoding = tiktoken.get_encoding("cl100k_base")
chunks = read(packet/"chunks.json")
sources = read(packet/"source-units.json")["sources"]
checks = []
for case, row in zip(read(packet/"cases.json"), read(prior_run/"results.json"), strict=True):
    assert case["id"] == row["id"] and case["question"] == row["question"]
    previews = next(event["contexts"] for event in row["events"] if event["type"] == "contexts")
    contexts = [next(chunk for chunk in chunks if source_preview(chunk) == preview) for preview in previews]
    payload = json.loads(request_content("evidence_select", {"question": case["question"], "unit_sources": sources},
        {"contexts": contexts}, "", {"mode": "answer_card_pipeline"}))
    payload["question_parts"] = question_parts(case["question"])
    tokens = len(encoding.encode(selector))+len(encoding.encode(json.dumps(payload, ensure_ascii=False)))
    assert tokens <= 5400
    checks.append({"id": case["id"], "selector_input_tokens": tokens, "original_question_sha256": hashlib.sha256(case["question"].encode()).hexdigest()})
write(packet/"input-preflight.json", {"cases": checks, "max_input": max(c["selector_input_tokens"] for c in checks),
    "scope": "Previous actual retrieval contexts, not new retrieval/semantic acceptance"})
write(packet/"funding-preflight.json", {"prior_accounted_usd": b["accounted_with_reservations_usd"],
    "unknown_reservations": b["unknown_fee_reservations"], "max_calls": 64, "allocation_usd": .32,
    "max_accounted_usd": b["accounted_with_reservations_usd"]+.32, "standing_authorization_usd": 3,
    "scope": "PublicNIST only; original32 route+selector, Flash disabledthinking1024/no retries"})
write(packet/"delta.json", {"original32_cases_sha256": sha(original/"cases.json"), "original_corpus_sha256": sha(original/"chunks.json"),
    "protocol_selector_renderer_source_units_translations_unchanged_since_8plus8": True,
    "no_model_labels_or_answers_indexed": True, "no_default_change_or_human_gold": True})
m = read(source/"manifest.json")
m.update(max_calls=64, budget_usd=.32, role="Original32 actual app regression of frozen question-parts protocol; old input and standards preserved.",
    original_cases_sha256=sha(original/"cases.json"), base_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    acceptance="All original32 completed, target/citation/SSE/SQL/receipts strict audit and separate original-source semantic review. AI development only.")
m["frozen_sha256"] = {path.name: sha(path) for path in packet.iterdir() if path.is_file()}
write(packet/"manifest.json", m)
write(packet/"authorization.json", {"max_calls": 64, "budget_usd": .32, "manifest_sha256": sha(packet/"manifest.json")})
state["active_experiment"] = {"packet": packet.relative_to(ROOT).as_posix(),
    "output": f"docs/engineering-delivery/{packet.name}-live", "status": "frozen_original32", "max_calls": 64, "budget_usd": .32}
state["pending_batches"] = [state["active_experiment"]]
b["pending_allocations"] = [{"batch": packet.name, "max_calls": 64, "budget_usd": .32}]
write(statefile, state)
print(json.dumps({"cases": 32, "max_calls": 64, "allocation_usd": .32,
                  "max_input": max(c["selector_input_tokens"] for c in checks), "max_accounted_usd": b["accounted_with_reservations_usd"]+.32}))
