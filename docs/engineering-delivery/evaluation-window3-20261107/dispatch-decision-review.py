"""One narrow saved-answer review batch inside the standing $3 grant, no retry."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PACKET = ROOT / "evaluation/nist-decision-calibration-v2-20261107"
OUTPUT = ROOT / "docs/engineering-delivery/nist-decision-calibration-v2-20261107-live"
STATE = HERE / "continuation.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


state = read(STATE)
budget = state["budget"]
assert not budget["pending_allocations"] and not state["pending_batches"]
assert budget["accounted_with_reservations_usd"] + .08 <= budget["approved_total_usd"] == 3
manifest = read(PACKET / "manifest.json")
assert manifest["paid_proposal"]["calls"] == 8 and manifest["paid_proposal"]["budget_usd"] == .08
assert sha(ROOT / "scripts/eval_nist_batch.py") == manifest["executor_sha256"]
assert type(manifest["max_estimated_input_tokens"]) is int and 1 <= manifest["max_estimated_input_tokens"] <= 5500
before = sha(ROOT / "data/cisp_qa.db")
assert before == state["default_db_sha256"]
batch = {"batch": PACKET.name, "max_calls": 8, "budget_usd": .08}
budget["pending_allocations"] = [batch]
state["pending_batches"] = [batch]
state["active_experiment"] = {**batch, "status": "running_once_saved_answers_only",
    "controller_pid": os.getpid(), "started_utc": datetime.now(timezone.utc).isoformat(),
    "packet": PACKET.relative_to(ROOT).as_posix(), "output": OUTPUT.relative_to(ROOT).as_posix()}
write(STATE, state)
with (HERE / "operator-private/decision-review-v2-console.log").open("x", encoding="utf-8") as console:
    result = subprocess.run([sys.executable, "-m", "scripts.eval_nist_batch", "--packet", str(PACKET),
        "--output", str(OUTPUT), "--execute"], cwd=ROOT,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}, stdout=console, stderr=subprocess.STDOUT)
summary = read(OUTPUT / "summary.json") if (OUTPUT / "summary.json").exists() else {
    "provider_calls": 0, "cost_usd_conservative": 0, "cost_unknown": True}
known, unknown = summary["cost_usd_conservative"], summary.get("cost_unknown", True)
after = sha(ROOT / "data/cisp_qa.db")
OUTPUT.mkdir(exist_ok=True)
write(OUTPUT / "default-db.json", {"before": before, "after": after, "unchanged": before == after})
write(OUTPUT / "controller-receipt.json", {"exit_code": result.returncode,
    "executor_unchanged": sha(ROOT / "scripts/eval_nist_batch.py") == manifest["executor_sha256"],
    "controller_sha256": sha(Path(__file__))})
state = read(STATE)
budget = state["budget"]
budget["new_batches"].append({"id": PACKET.name, "calls": summary["provider_calls"], "estimated_cost_usd": known, "unknown_charge": unknown})
if unknown:
    budget["unknown_fee_reservations"].append({"batch": PACKET.name, "allocation_held_usd": .08,
        "additional_reservation_usd": .08 - known, "status": "Retain entire allocation, no retry"})
budget["dispatch_attempts"] = budget["anchor_dispatch_attempts"] + sum(row["calls"] for row in budget["new_batches"])
budget["known_estimate_usd"] = round(budget["anchor_estimate_usd"] + sum(row["estimated_cost_usd"] for row in budget["new_batches"]), 10)
budget["unknown_current_charge"] = any(row["unknown_charge"] for row in budget["new_batches"])
budget["accounted_with_reservations_usd"] = round(budget["known_estimate_usd"] + sum(row["additional_reservation_usd"] for row in budget["unknown_fee_reservations"]), 10)
budget["unallocated_authorization_usd"] = round(3 - budget["accounted_with_reservations_usd"], 10)
budget["pending_allocations"] = []
state["pending_batches"] = []
state["active_experiment"]["status"] = "completed_pending_calibration_audit" if result.returncode == 0 else "failed_preserved_no_retry"
write(STATE, state)
assert before == after
print(json.dumps({"exit_code": result.returncode, "calls": summary["provider_calls"], "known_estimate_usd": known, "unknown": unknown}))
sys.exit(result.returncode)
