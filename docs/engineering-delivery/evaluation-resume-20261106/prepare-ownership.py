"""Freeze a source-only parameter ownership experiment; never dispatch APIs."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.eval_nist_batch import request_content  # noqa: E402
import tiktoken  # noqa: E402


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


source = ROOT / "evaluation/nist-reading-guidance-v4-20261107"
packet = ROOT / "evaluation/nist-native-ownership-v5r2-20261107"
packet.mkdir(exist_ok=False)
manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
for name in manifest["frozen_sha256"]:
    if name != "funding-preflight.json":
        shutil.copyfile(source / name, packet / name)
helper = ROOT / "scripts/inline_oscal_parameters.py"
shutil.copyfile(helper, packet / "parameter-helper.py")
manifest["native_parameter_bindings"] = True
manifest["role"] = "Same v4 questions/source/aids/prompts/temperature/guidance/renderer; add literal parameter-to-original-clause references and lossless compact JSON whitespace, no inferred scope or expected answers. v5 preparation5617tokens failed5500limit with zeroAPI; preserved. Development diagnostic, not independent test."
manifest["executor_sha256"] = digest(ROOT / "scripts/eval_nist_batch.py")
state_path = ROOT / "docs/engineering-delivery/evaluation-resume-20261106/continuation.json"
state = json.loads(state_path.read_text(encoding="utf-8"))
budget = state["budget"]
held = budget["known_estimate_usd"] + sum(x["additional_reservation_usd"] for x in budget["unknown_fee_reservations"])
assert abs(held - budget["accounted_with_reservations_usd"]) < 1e-9
assert not budget["pending_allocations"] and held + 0.08 <= 3
write(packet / "funding-preflight.json", {"known_estimate_usd": budget["known_estimate_usd"],
    "unknown_reservations": budget["unknown_fee_reservations"], "prior_accounted_usd": held,
    "new_frozen_allocation_usd": 0.08, "maximum_accounted_usd": held + 0.08,
    "approved_total_usd": 3, "authorization": "standing user3USD, this distinct batch; old unknown fee remains reserved"})
write(packet / "runtime-source-hashes.json", {"scripts/inline_oscal_parameters.py": digest(helper)})
manifest["frozen_sha256"] = {p.name: digest(p) for p in packet.iterdir() if p.is_file()}
write(packet / "manifest.json", manifest)
encoding = tiktoken.get_encoding("cl100k_base")
cases = json.loads((packet / "cases.json").read_text(encoding="utf-8"))
prompts = json.loads((packet / "prompts.json").read_text(encoding="utf-8"))
estimates = []
for case in cases:
    row = {"contexts": case["fixed_contexts"]}
    for stage in ("answer_baseline", "answer_structured"):
        content = request_content(stage, case, row, "", manifest)
        estimate = len(encoding.encode(content)) + len(encoding.encode(prompts[stage + "_system"]))
        estimates.append({"id": case["id"], "stage": stage, "tokens": estimate})
write(packet / "input-preflight.json", {"estimates": estimates, "maximum_input": max(x["tokens"] for x in estimates),
    "limit": 5500, "calls": 16, "same_questions_and_sources": digest(packet / "cases.json") == digest(source / "cases.json")})
assert max(x["tokens"] for x in estimates) <= 5500
state["active_experiment"] = {"packet": str(packet.relative_to(ROOT)).replace("\\", "/"),
    "output": "docs/engineering-delivery/nist-native-ownership-20261107-live", "status": "frozen_preflight_passed", "max_calls": 16, "budget_usd": 0.08}
state["pending_batches"] = [state["active_experiment"]]
budget["pending_allocations"] = [{"batch": packet.name, "max_calls": 16, "budget_usd": 0.08}]
state["next"] = "Run frozen native ownership diagnostic, preserve unknown reservation and original semantic criteria; never promote without repeat/negative/full32/mixed4."
write(state_path, state)
write(packet / "authorization.json", {"max_calls": 16, "budget_usd": 0.08, "manifest_sha256": digest(packet / "manifest.json")})
print(json.dumps({"packet": packet.name, "max_input": max(x["tokens"] for x in estimates), "funding_max": held + 0.08}))
