"""Prove zero dispatch, preserve failure, move only synthetic output outside data."""

import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def read(p):
    return json.loads(p.read_text(encoding="utf8"))


def write(p, v):
    p.write_text(json.dumps(v, ensure_ascii=False, indent=2) + "\n", encoding="utf8")


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


old = ROOT / "data/review_packets/nist-mixed-cards-regression-20261107"
out = ROOT / "data/evaluation/nist-mixed-cards-regression-20261107-live"
console = HERE / "operator-private/mixed-cards-console.log"
assert "prepare(data)" in console.read_text(
    encoding="utf8"
) and "demo output must not contain or be inside the default data directory" in console.read_text(
    encoding="utf8"
)
assert not (out / "receipts.jsonl").exists() and not (out / "summary.json").exists()
assert sha(out / "executor.py") == read(old / "manifest.json")["executor_sha256"]
text = (out / "executor.py").read_text(encoding="utf8")
assert (
    text.index("prepare(data)")
    < text.index("recorder = Receipts(")
    < text.index('client.post("/chat/stream"')
)
proof = {
    "failure": "prepare_demo intentionally rejects synthetic demo output inside default data tree",
    "provider_dispatches": 0,
    "cost_usd": 0,
    "evidence": "Frozen executor prepare at111 precedes server import/Receipts/HTTP; no summary or receipts; local traceback matches guard.",
    "console_sha256": sha(console),
    "executor_sha256": sha(out / "executor.py"),
    "default_database_unchanged": read(out / "default-db.json")["unchanged"],
    "old_failure_and_lock_retained": True,
    "fix": "Keep source packet in ignored data; put isolated synthetic output in ignored operator-private outside data tree. No application guard weakened.",
}
write(HERE / "mixed-initialization-zero-dispatch.json", proof)
statefile = HERE / "continuation.json"
state = read(statefile)
b = state["budget"]
matched = [r for r in b["new_batches"] if r["id"] == old.name]
assert len(matched) == 1 and matched[0]["calls"] == 0
matched[0]["unknown_charge"] = False
matched[0]["zero_dispatch_proof"] = (
    "docs/engineering-delivery/evaluation-window2-20261107/mixed-initialization-zero-dispatch.json"
)
b["unknown_fee_reservations"] = [r for r in b["unknown_fee_reservations"] if r["batch"] != old.name]
b["unknown_current_charge"] = any(r["unknown_charge"] for r in b["new_batches"])
b["accounted_with_reservations_usd"] = round(
    b["known_estimate_usd"]
    + sum(r["additional_reservation_usd"] for r in b["unknown_fee_reservations"]),
    10,
)
b["unallocated_authorization_usd"] = round(3 - b["accounted_with_reservations_usd"], 10)
assert b["accounted_with_reservations_usd"] + 0.08 <= 3 and not b["pending_allocations"]
packet = old.with_name(old.name + "-r2")
packet.mkdir(exist_ok=False)
for p in old.iterdir():
    if p.name not in {
        "manifest.json",
        "authorization.json",
        "dispatch.lock",
        "funding-preflight.json",
    }:
        shutil.copyfile(p, packet / p.name)
write(
    packet / "funding-preflight.json",
    {
        "prior_accounted_usd": b["accounted_with_reservations_usd"],
        "unknown_reservations": b["unknown_fee_reservations"],
        "max_calls": 8,
        "allocation_usd": 0.08,
        "max_accounted_usd": b["accounted_with_reservations_usd"] + 0.08,
        "total_authorization_usd": 3,
    },
)
m = read(old / "manifest.json")
m["role"] += " Only synthetic output relocated after proven zero-dispatch guard failure."
m["frozen_sha256"] = {p.name: sha(p) for p in packet.iterdir() if p.is_file()}
write(packet / "manifest.json", m)
write(
    packet / "authorization.json",
    {"max_calls": 8, "budget_usd": 0.08, "manifest_sha256": sha(packet / "manifest.json")},
)
newoutput = "docs/engineering-delivery/evaluation-window2-20261107/operator-private/mixed-cards-regression-r2-live"
state["active_experiment"] = {
    "packet": packet.relative_to(ROOT).as_posix(),
    "output": newoutput,
    "status": "scope_verified_frozen_after_zero_dispatch_guard",
    "max_calls": 8,
    "budget_usd": 0.08,
}
state["pending_batches"] = [state["active_experiment"]]
b["pending_allocations"] = [{"batch": packet.name, "max_calls": 8, "budget_usd": 0.08}]
write(statefile, state)
controller = (
    (HERE / "dispatch-mixed-regression.py")
    .read_text(encoding="utf8")
    .replace(
        "data/review_packets/nist-mixed-cards-regression-20261107",
        "data/review_packets/nist-mixed-cards-regression-20261107-r2",
    )
    .replace("data/evaluation/nist-mixed-cards-regression-20261107-live", newoutput)
    .replace("mixed-cards-console.log", "mixed-cards-r2-console.log")
)
(HERE / "dispatch-mixed-regression-r2.py").write_text(controller, encoding="utf8")
print(json.dumps(proof))
