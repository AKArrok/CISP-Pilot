"""Freeze original32 plus original8 without rewriting questions or criteria."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


source = ROOT/"evaluation/nist-public-http-question-groups-focus-v3-20261107"
original32 = ROOT/"evaluation/nist-public-http-question-parts-regression32-20261107"
original8 = ROOT/"evaluation/nist-public-http-question-parts-v1-20261107"
packet = ROOT/"evaluation/nist-public-http-question-groups-regression40-v3-20261107"
statefile = HERE/"continuation.json"
state = read(statefile)
b = state["budget"]
assert not b["pending_allocations"] and not state["pending_batches"]
assert b["accounted_with_reservations_usd"]+.40 <= b["approved_total_usd"] == 3
assert state["question_groups_v3"]["source_supported"] == 4
assert all(sha(ROOT/name) == digest for name, digest in read(source/"runtime-source-hashes.json").items())
packet.mkdir(exist_ok=False)
for name in ("chunks.json", "prompts.json", "card-selector.json", "source-units.json", "translations.json", "translation-review.json", "contract.py"):
    shutil.copyfile(source/name, packet/name)
first, second = read(original32/"cases.json"), read(original8/"cases.json")
cases = first+second
assert len(first) == 32 and len(second) == 8 and len({case["id"] for case in cases}) == 40
assert len({case["thread_group"] for case in cases}) == 40
write(packet/"cases.json", cases)
assert read(packet/"cases.json")[:32] == first and read(packet/"cases.json")[32:] == second
write(packet/"acceptance.json", {"scope": "Original32 and original8 criteria inherited verbatim; cohort scores separately",
    "original32": read(original32/"acceptance.json"), "original8": read(original8/"acceptance.json"),
    "no_question_label_threshold_change": True, "human_gold": False})
controller = (HERE/"dispatch-question-parts-v1.py").read_text(encoding="utf-8").replace(
    "nist-public-http-question-parts-v1-20261107", packet.name).replace(
    "question-parts-v1-console.log", "question-groups-regression40-v3-console.log").replace("16", "80").replace(".12", ".40")
controller_file = HERE/"dispatch-regression40-v3.py"
controller_file.write_text(controller, encoding="utf-8")
dependencies = read(source/"runtime-source-hashes.json")
dependencies[controller_file.relative_to(ROOT).as_posix()] = "pending"
write(packet/"runtime-source-hashes.json", {name: sha(ROOT/name) for name in dependencies})
write(packet/"funding-preflight.json", {"prior_accounted_usd": b["accounted_with_reservations_usd"],
    "unknown_reservations": b["unknown_fee_reservations"], "max_calls": 80, "allocation_usd": .40,
    "max_accounted_usd": b["accounted_with_reservations_usd"]+.40, "standing_authorization_usd": 3,
    "scope": "Only public NIST, original40 route+selector; Flash disabledthinking1024/no retries"})
write(packet/"delta.json", {"source_protocol_renderer_units_translations_identical_to_focus5": True,
    "original32_cases_sha256": sha(original32/"cases.json"), "original8_cases_sha256": sha(original8/"cases.json"),
    "all_case_dicts_preserved": True, "all_old_failures_retained": True, "default_promoted": False})
m = read(source/"manifest.json")
m.update(max_calls=80, budget_usd=.40, role="Full original32+original8 actual app regression of focus-v3; no question/criteria edits.",
    original_cases_sha256=sha(original32/"cases.json"), base_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    acceptance="All40 execution/target/source/SSE/SQL/receipt audit plus separate complete source review per original cohort standards; AI development only.")
m["frozen_sha256"] = {path.name: sha(path) for path in packet.iterdir() if path.is_file()}
write(packet/"manifest.json", m)
write(packet/"authorization.json", {"max_calls": 80, "budget_usd": .40, "manifest_sha256": sha(packet/"manifest.json")})
state["active_experiment"] = {"packet": packet.relative_to(ROOT).as_posix(),
    "output": f"docs/engineering-delivery/{packet.name}-live", "status": "frozen_original40_v3", "max_calls": 80, "budget_usd": .40}
state["pending_batches"] = [state["active_experiment"]]
b["pending_allocations"] = [{"batch": packet.name, "max_calls": 80, "budget_usd": .40}]
write(statefile, state)
print(json.dumps({"cases": 40, "max_calls": 80, "allocation_usd": .40, "max_accounted_usd": b["accounted_with_reservations_usd"]+.40}))
