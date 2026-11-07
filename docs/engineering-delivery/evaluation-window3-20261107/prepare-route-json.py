"""Freeze the exact route-degraded question; only routing serialization changed."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


source = ROOT / "evaluation/nist-public-http-question-groups-regression40-v3-20261107"
packet = ROOT / "evaluation/nist-public-http-route-json-20261107"
statefile = HERE / "continuation.json"
state = read(statefile)
budget = state["budget"]
assert not budget["pending_allocations"] and not state["pending_batches"]
assert budget["accounted_with_reservations_usd"] + .02 <= 3
packet.mkdir(exist_ok=False)
for name in ("chunks.json", "prompts.json", "card-selector.json", "source-units.json", "translations.json", "translation-review.json", "contract.py", "acceptance.json"):
    shutil.copyfile(source / name, packet / name)
cases = [case for case in read(source / "cases.json") if case["id"] == "ac-8-new-05"]
assert len(cases) == 1
write(packet / "cases.json", cases)
controller = (HERE / "dispatch-regression40-v3.py").read_text(encoding="utf-8").replace(
    source.name, packet.name).replace("question-groups-regression40-v3-console.log", "route-json-console.log").replace("80", "2").replace(".40", ".02")
controller_file = HERE / "dispatch-route-json.py"
controller_file.write_text(controller, encoding="utf-8")
dependencies = read(source / "runtime-source-hashes.json")
dependencies[controller_file.relative_to(ROOT).as_posix()] = "pending"
write(packet / "runtime-source-hashes.json", {name: sha(ROOT / name) for name in dependencies})
write(packet / "delta.json", {"changed": ["agents/graph.py: DeepSeek route explicitly JSON mode", "llms.py: optional method preserving default callers"],
    "unchanged": ["question", "acceptance", "101 corpus", "selector", "contract", "thresholds"],
    "not_quality_acceptance": True, "old_route_failure_preserved": True})
write(packet / "funding-preflight.json", {"prior_accounted_usd": budget["accounted_with_reservations_usd"],
    "unknown_reservations": budget["unknown_fee_reservations"], "max_calls": 2, "allocation_usd": .02,
    "max_accounted_usd": budget["accounted_with_reservations_usd"] + .02, "standing_authorization_usd": 3})
manifest = read(source / "manifest.json")
manifest.update(max_calls=2, budget_usd=.02, role="Exact original route-degraded question, route JSON serialization fix only")
manifest["frozen_sha256"] = {path.name: sha(path) for path in packet.iterdir() if path.is_file()}
write(packet / "manifest.json", manifest)
write(packet / "authorization.json", {"max_calls": 2, "budget_usd": .02, "manifest_sha256": sha(packet / "manifest.json")})
state["active_experiment"] = {"packet": packet.relative_to(ROOT).as_posix(), "output": f"docs/engineering-delivery/{packet.name}-live", "status": "frozen_same_route_case", "max_calls": 2, "budget_usd": .02}
state["pending_batches"] = [state["active_experiment"]]
budget["pending_allocations"] = [{"batch": packet.name, "max_calls": 2, "budget_usd": .02}]
write(statefile, state)
print(json.dumps({"cases": 1, "calls": 2, "cap_usd": .02}))
