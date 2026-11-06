"""One frozen batch: source/default DB hashes before and after, no retries."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
packet = ROOT / "evaluation/nist-unit-cards-repeat-v1-20261107"
output = ROOT / "docs/engineering-delivery/nist-unit-cards-repeat-v1-20261107-live"
state_path = Path(__file__).with_name("continuation.json")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


dependencies = read(packet / "runtime-source-hashes.json")
assert all(sha(ROOT / name) == value for name, value in dependencies.items())
state = read(state_path)
before = sha(ROOT / "data/cisp_qa.db")
assert before == state["default_db_sha256"]
state["active_experiment"]["status"] = "dispatching_once"
write(state_path, state)
env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
result = subprocess.run([sys.executable, str(ROOT / "scripts/eval_nist_batch.py"), "--packet", str(packet),
    "--output", str(output), "--execute"], cwd=ROOT, env=env)
after = sha(ROOT / "data/cisp_qa.db")
unchanged = all(sha(ROOT / name) == value for name, value in dependencies.items())
write(output / "default-db.json", {"before": before, "after": after, "unchanged": before == after})
write(output / "runtime-source-audit.json", {"expected": dependencies, "unchanged": unchanged})
summary = read(output / "summary.json")
state = read(state_path)
budget = state["budget"]
known = summary.get("known_cost_lower_bound_usd", summary["cost_usd_conservative"])
unknown = summary.get("cost_unknown", False)
budget["new_batches"].append({"id": packet.name, "calls": summary["provider_calls"],
    "estimated_cost_usd": known, "unknown_charge": unknown})
if unknown:
    budget["unknown_fee_reservations"].append({"batch": packet.name, "unknown_attempts": 1,
        "known_estimate_usd": known, "allocation_held_usd": .08, "additional_reservation_usd": .08 - known,
        "status": "unknown actual charge, full allocation retained"})
budget["dispatch_attempts"] = budget["anchor_dispatch_attempts"] + sum(x["calls"] for x in budget["new_batches"])
budget["known_estimate_usd"] = round(budget["anchor_estimate_usd"] + sum(x["estimated_cost_usd"] for x in budget["new_batches"]), 10)
budget["unknown_current_charge"] = any(x["unknown_charge"] for x in budget["new_batches"])
budget["accounted_with_reservations_usd"] = round(budget["known_estimate_usd"] + sum(x["additional_reservation_usd"] for x in budget["unknown_fee_reservations"]), 10)
budget["unallocated_authorization_usd"] = round(3 - budget["accounted_with_reservations_usd"], 10)
budget["pending_allocations"] = []
state["pending_batches"] = []
state["active_experiment"]["status"] = "completed_pending_original_source_review" if result.returncode == 0 else "stopped_failure_preserved_no_retry"
write(state_path, state)
assert before == after and unchanged
sys.exit(result.returncode)
