"""One frozen public HTTP batch; reconcile receipts and retain unknown allocation."""
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
packet = ROOT / "evaluation/nist-public-http-scenarios-r2-20261107"
output = ROOT / "docs/engineering-delivery/nist-public-http-scenarios-r2-20261107-live"
statefile = HERE / "continuation.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


manifest = read(packet / "manifest.json")
assert manifest["max_calls"] == 16 and manifest["budget_usd"] == .12 and manifest["answer_strategy"] == "source_cards"
dependencies = read(packet / "runtime-source-hashes.json")
assert all(sha(ROOT / name) == digest for name, digest in dependencies.items())
state = read(statefile)
budget = state["budget"]
assert budget["pending_allocations"] == [{"batch": packet.name, "max_calls": 16, "budget_usd": .12}]
assert budget["accounted_with_reservations_usd"] + .12 <= budget["approved_total_usd"]
before = sha(ROOT / "data/cisp_qa.db")
assert before == state["default_db_sha256"]
state["active_experiment"]["status"] = "running_once_full_application"
state["active_experiment"]["controller_pid"] = os.getpid()
state["active_experiment"]["controller_started_utc"] = datetime.now(timezone.utc).isoformat()
state["active_experiment"]["controller_script_sha256"] = sha(Path(__file__))
write(statefile, state)
private = HERE / "operator-private"
private.mkdir(exist_ok=True)
with (private / "scenarios-console.log").open("x", encoding="utf-8") as console:
    process = subprocess.run([sys.executable, "-m", "scripts.evaluate_public_http", "--packet", str(packet),
        "--output", str(output), "--execute"], cwd=ROOT, env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        stdout=console, stderr=subprocess.STDOUT)
after = sha(ROOT / "data/cisp_qa.db")
unchanged = all(sha(ROOT / name) == digest for name, digest in dependencies.items())
output.mkdir(exist_ok=True)
(output / ".gitignore").write_text("data/\noperator-private/\nserver.log\n", encoding="utf-8")
write(output / "runtime-source-audit.json", {"expected": dependencies, "unchanged": unchanged})
write(output / "controller-default-db.json", {"before": before, "after": after, "unchanged": before == after})
summary = read(output / "summary.json") if (output / "summary.json").exists() else {
    "dispatch_attempts": 0, "known_cost_estimate_usd": 0, "cost_unknown": True,
    "status": "missing_summary_unknown_dispatch_count_no_retry"}
state = read(statefile)
budget = state["budget"]
known, unknown = summary["known_cost_estimate_usd"], summary["cost_unknown"]
budget["new_batches"].append({"id": packet.name, "calls": summary["dispatch_attempts"],
    "estimated_cost_usd": known, "unknown_charge": unknown})
if unknown:
    budget["unknown_fee_reservations"].append({"batch": packet.name, "unknown_attempts": "at least1 or unconfirmed if summary absent",
        "known_estimate_usd": known, "allocation_held_usd": .12, "additional_reservation_usd": .12-known,
        "status": "Full batch allocation retained; no automatic retry"})
budget["dispatch_attempts"] = budget["anchor_dispatch_attempts"] + sum(row["calls"] for row in budget["new_batches"])
budget["known_estimate_usd"] = round(budget["anchor_estimate_usd"] + sum(row["estimated_cost_usd"] for row in budget["new_batches"]), 10)
budget["unknown_current_charge"] = any(row["unknown_charge"] for row in budget["new_batches"])
budget["accounted_with_reservations_usd"] = round(budget["known_estimate_usd"] + sum(row["additional_reservation_usd"] for row in budget["unknown_fee_reservations"]), 10)
budget["unallocated_authorization_usd"] = round(budget["approved_total_usd"]-budget["accounted_with_reservations_usd"], 10)
budget["pending_allocations"] = []
state["pending_batches"] = []
state["active_experiment"]["status"] = "http_completed_pending_strict_audit_and_semantics" if process.returncode == 0 else "http_stopped_failure_preserved_no_retry"
write(statefile, state)
assert before == after and unchanged
print(json.dumps({"exit_code": process.returncode, "attempts": summary["dispatch_attempts"], "known_cost": known,
    "unknown_charge": unknown, "default_db_unchanged": True}), flush=True)
sys.exit(process.returncode)
