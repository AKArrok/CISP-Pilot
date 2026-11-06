"""Restore one existing baseline principle in the selector; freeze all16 cases."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.eval_nist_batch import request_content  # noqa: E402
import tiktoken  # noqa: E402


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


positive = ROOT / "evaluation/nist-unit-cards-v1-20261107"
negative = ROOT / "evaluation/nist-unit-cards-negatives-v1-20261107"
packet = ROOT / "evaluation/nist-unit-cards-actionguard-v2-20261107"
packet.mkdir(exist_ok=False)
for name in ("chunks.json", "source-units.json", "translations.json", "translation-review.json", "runtime-source-hashes.json", "contract.py"):
    shutil.copyfile(negative / name, packet / name)
shutil.copyfile(positive / "original-cases.json", packet / "original-positive-cases.json")
shutil.copyfile(negative / "acceptance.json", packet / "negative-acceptance.json")
cases = read(positive / "cases.json") + read(negative / "cases.json")
assert len({case["id"] for case in cases}) == 16
write(packet / "cases.json", cases)
rule = "区分待赋值的参数与必须执行的动作；参数值待定不表示动作可选。"
prompts = read(negative / "prompts.json")
assert rule in prompts["answer_baseline_system"]
prompts["evidence_select_system"] += "\n" + rule
write(packet / "prompts.json", prompts)
write(packet / "delta.json", {"only_selector_prompt_delta": rule,
    "origin": "Existing baseline rule2, not new control/question-specific hint",
    "same_positive8_sha256": sha(positive / "cases.json"), "same_negative8_sha256": sha(negative / "cases.json"),
    "baseline_prompt_unchanged": True, "host_contract_and_source_translations_unchanged_from_negative_batch": True,
    "criteria_unchanged": True})
sf = Path(__file__).with_name("continuation.json")
state = read(sf)
b = state["budget"]
assert not b["pending_allocations"] and not state["pending_batches"]
held = b["known_estimate_usd"] + sum(row["additional_reservation_usd"] for row in b["unknown_fee_reservations"])
assert abs(held-b["accounted_with_reservations_usd"]) < 1e-9 and held+.16 <= b["approved_total_usd"]
write(packet / "funding-preflight.json", {"known_estimate_usd": b["known_estimate_usd"], "unknown_reservations": b["unknown_fee_reservations"],
    "prior_accounted_usd": held, "new_allocation_usd": .16, "max_accounted_usd": held+.16, "approved_total_usd": 3,
    "authorization": "standing user3USD, new32calls, no reuse of consumed allocation or old-lock retry"})
manifest = read(negative / "manifest.json")
manifest["role"] = "Same originalpositive8+sameboundary8, one generic baseline principle restored in selector; old7/8failure retained, no criterion/index/source changes."
manifest["paid_proposal"]["calls"] = 32
manifest["paid_proposal"]["budget_usd"] = .16
manifest["paid_proposal"]["scope"] = "Only same publicNIST sources, positive8+boundary8 baseline and selector"
encoding = tiktoken.get_encoding("cl100k_base")
estimates = []
for case in cases:
    for stage in ("answer_baseline", "evidence_select"):
        content = request_content(stage, case, {"contexts": case["fixed_contexts"]}, "", manifest)
        estimates.append({"id": case["id"], "stage": stage,
            "tokens": len(encoding.encode(content)) + len(encoding.encode(prompts[stage+"_system"]))})
assert max(row["tokens"] for row in estimates) <= 5500
write(packet / "input-preflight.json", {"estimates": estimates, "max_input": max(row["tokens"] for row in estimates),
    "same_questions_sources_criteria": True, "positive_cases": 8, "boundary_cases": 8, "model_calls": 0})
manifest["frozen_sha256"] = {path.name: sha(path) for path in packet.iterdir() if path.is_file()}
write(packet / "manifest.json", manifest)
write(packet / "authorization.json", {"max_calls": 32, "budget_usd": .16, "manifest_sha256": sha(packet / "manifest.json")})
state["active_experiment"] = {"packet": packet.relative_to(ROOT).as_posix(),
    "output": "docs/engineering-delivery/nist-unit-cards-actionguard-v2-20261107-live", "status": "frozen_preflight_passed", "max_calls": 32, "budget_usd": .16}
state["pending_batches"] = [state["active_experiment"]]
b["pending_allocations"] = [{"batch": packet.name, "max_calls": 32, "budget_usd": .16}]
write(sf, state)
print(json.dumps({"packet": packet.name, "max_input": max(row["tokens"] for row in estimates), "accounted_max": held+.16}))
