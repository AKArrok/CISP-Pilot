"""Freeze original8 boundary questions plus original4 plan boundaries."""
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


source = ROOT/"evaluation/nist-public-http-question-parts-missing-v2-20261107"
first = ROOT/"evaluation/nist-public-http-cards-boundaries-20261107"
second = ROOT/"evaluation/nist-public-http-plan-boundaries-20261107"
packet = ROOT/"evaluation/nist-public-http-question-parts-boundary12-v2-20261107"
statefile = HERE/"continuation.json"
state = read(statefile)
b = state["budget"]
assert not b["pending_allocations"] and not state["pending_batches"]
assert b["accounted_with_reservations_usd"]+.16 <= b["approved_total_usd"] == 3
assert state["regression40_v2"]["source_supported"] == 40
assert all(sha(ROOT/name) == digest for name, digest in read(source/"runtime-source-hashes.json").items())
assert sha(source/"chunks.json") == sha(first/"chunks.json") == sha(second/"chunks.json")
packet.mkdir(exist_ok=False)
for name in ("chunks.json", "prompts.json", "card-selector.json", "source-units.json", "translations.json", "translation-review.json", "contract.py"):
    shutil.copyfile(source/name, packet/name)
one, two = read(first/"cases.json"), read(second/"cases.json")
cases = one+two
assert len(one) == 8 and len(two) == 4 and len({case["id"] for case in cases}) == 12
assert len({case["thread_group"] for case in cases}) == 12
write(packet/"cases.json", cases)
write(packet/"acceptance.json", {"original8": read(first/"acceptance.json"), "original4": read(second/"acceptance.json"),
    "scope": "Original cases/semantic criteria retained; no new independent-unseen/human-gold claim"})
controller = (HERE/"dispatch-question-parts-v1.py").read_text(encoding="utf-8").replace(
    "nist-public-http-question-parts-v1-20261107", packet.name).replace(
    "question-parts-v1-console.log", "question-parts-boundary12-v2-console.log").replace("16", "24").replace(".12", ".16")
controller_file = HERE/"dispatch-boundary12-v2.py"
controller_file.write_text(controller, encoding="utf-8")
dependencies = read(source/"runtime-source-hashes.json")
dependencies[controller_file.relative_to(ROOT).as_posix()] = "pending"
write(packet/"runtime-source-hashes.json", {name: sha(ROOT/name) for name in dependencies})
write(packet/"funding-preflight.json", {"prior_accounted_usd": b["accounted_with_reservations_usd"],
    "unknown_reservations": b["unknown_fee_reservations"], "max_calls": 24, "allocation_usd": .16,
    "max_accounted_usd": b["accounted_with_reservations_usd"]+.16, "standing_authorization_usd": 3,
    "scope": "Only public NIST, same original12 route+selector; Flash disabledthinking1024/no retries"})
write(packet/"delta.json", {"original8_cases_sha256": sha(first/"cases.json"), "original4_cases_sha256": sha(second/"cases.json"),
    "same_v2_protocol_source_units_translations": True, "original_questions_criteria_unchanged": True, "old_failures_retained": True})
m = read(source/"manifest.json")
m.update(max_calls=24, budget_usd=.16, role="Original12 boundary actual app regression after complete original40, unchanged frozenv2.",
    original_cases_sha256=sha(first/"cases.json"), base_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    acceptance="All12 execution/target/citation/SSE/SQL/receipt audit and separate source-supported condition/absence/permission review per unchanged criteria.")
m["frozen_sha256"] = {path.name: sha(path) for path in packet.iterdir() if path.is_file()}
write(packet/"manifest.json", m)
write(packet/"authorization.json", {"max_calls": 24, "budget_usd": .16, "manifest_sha256": sha(packet/"manifest.json")})
state["active_experiment"] = {"packet": packet.relative_to(ROOT).as_posix(),
    "output": f"docs/engineering-delivery/{packet.name}-live", "status": "frozen_boundary12_v2", "max_calls": 24, "budget_usd": .16}
state["pending_batches"] = [state["active_experiment"]]
b["pending_allocations"] = [{"batch": packet.name, "max_calls": 24, "budget_usd": .16}]
write(statefile, state)
print(json.dumps({"cases": 12, "max_calls": 24, "allocation_usd": .16, "max_accounted_usd": b["accounted_with_reservations_usd"]+.16}))
