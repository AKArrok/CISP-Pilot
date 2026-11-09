"""Audit isolated Locust authentication and pre-password rate rejection evidence."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def audit(run, output=None):
    summary, plan = read(run / "summary.json"), read(run / "plan.json")
    traces = [json.loads(line) for line in (run / "scrypt.jsonl").read_text(encoding="utf-8").splitlines()]
    profile = [row for row in traces if row["phase"] == "profile"]
    boundary = read(run / "limit-boundary.json")
    constants = read(run / "security-constants.json")
    resources = read(run / "resources.json")
    probe, ready = read(run / "cache-probe.json"), read(run / "http-ready.json")
    stages = summary["stages"]
    log = (run / "server.log").read_bytes()
    error_markers = log.count(b"Traceback") + log.count(b"ERROR")
    checks = {
        "server_no_error_markers": error_markers == 0,
        "three_stages_complete": [stage["users"] for stage in stages] == [1, 4, 8],
        "login_session_logout_revocation26": len(stages) == 3 and all(
            {row["Name"] for row in stage["stats"]} == {"auth/login", "auth/me", "auth/logout", "auth/revoked-old-cookie"}
            and all(row["Request Count"] == stage["users"] * 2 and row["Failure Count"] == 0 for row in stage["stats"])
            for stage in stages),
        "four_invalid_then_limit429": [row["status"] for row in summary["negative_logins"]] == [401, 401, 401, 401, 429],
        "rejection_no_scrypt": boundary["status"] == 429 and boundary["derive_before"] == boundary["derive_after"] == len(traces),
        "all30_admitted_attempts_derived": len(profile) == 30 and not any(row["error"] for row in traces),
        "unchanged_security": constants == {"scrypt_n": 131072, "scrypt_r": 8, "scrypt_p": 1,
            "original_gate_capacity": 2, "gate_identity_preserved": True} and max(row["active_at_admission"] for row in traces) <= 2,
        "health200": bool(resources) and all(row["health_status"] == 200 for row in resources),
        "cache_probe_before_ready": probe["finished_monotonic"] < ready["time_monotonic"] < summary["negative_logins"][0]["started_monotonic"],
        "owned_processes_stopped": summary["resource_pid_verified"] and summary["runtime_stopped"] and summary["server_stopped"],
        "default_db_unchanged": summary["default_db_before"] == summary["default_db_after"] == plan["default_db_before"]
            == hashlib.sha256((ROOT / "data/cisp_qa.db").read_bytes()).hexdigest(),
        "frozen_files_unchanged": all(hashlib.sha256((run / name).read_bytes()).hexdigest() == value for name, value in plan["frozen_sha256"].items()),
        "runtime_code_unchanged": all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == value for name, value in plan["runtime_sha256"].items()),
        "no_model_calls": summary["model_calls"] == plan["model_calls"] == 0,
    }
    report = {"passed": all(checks.values()), "checks": checks,
        "latency_scope": "Local synthetic SQLite/TCP/Locust short diagnostics; not production SLA, no cross-run speedup claim",
        "login_success": sum(stage["login_count"] for stage in stages),
        "negative_attempts": summary["negative_logins"],
        "max_password_queue_seconds": max(row["wait_seconds"] for row in profile),
        "max_health_seconds": max(row["health_elapsed_seconds"] for row in resources),
        "peak_rss_mib": max(row["rss_bytes"] for row in resources) / 1024**2,
        "rejected_request_seconds": summary["negative_logins"][-1]["seconds"],
        "server_log_sha256": hashlib.sha256(log).hexdigest(), "server_error_markers": error_markers,
        "model_calls": 0, "model_cost_usd": 0}
    (output or run / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.run, args.output)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)
