"""Audit frozen synthetic business load; output aggregate evidence, never raw logs."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics


def write_once(path, value):
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != value:
            raise FileExistsError("Prior evidence differs; use a new run directory")
        return
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def audit(directory):
    def load(name):
        return json.loads((directory / name).read_text(encoding="utf-8"))

    plan, summary, stats = load("plan.json"), load("summary.json"), load("business-final.json")
    persistence, resources = load("persistence-audit.json"), load("resources.json")
    for name, digest in plan["frozen_sha256"].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest, name
    assert summary["default_db_unchanged"] and summary["runtime_pid_verified"] and summary["runtime_stopped"]
    assert all(row["num_requests"] > 0 and row["num_failures"] == 0 for row in stats)
    assert load("spawn.json")["users"] == plan["users"]
    counts = {row["name"]: row["num_requests"] for row in stats}
    assert persistence["roles"] == {"user": counts["chat/SSE"], "assistant": counts["chat/SSE"]}
    assert persistence["answer_receipts"] == counts["quiz/answer"]
    assert persistence["integrity"] == "ok" and persistence["max_message_chars"] < 100
    raw = (directory / "server.log").read_bytes()
    errors = {word: raw.count(word.encode()) for word in ("ERROR", "Traceback", "WinError")}
    assert not any(errors.values()), errors
    write_once(directory / "server-log-summary.json", {
        "sha256": hashlib.sha256(raw).hexdigest(), "error_counts": errors,
        "raw_log_published": False})
    answer = {"passed": True, "users": plan["users"], "seconds": plan["seconds"],
        "requests": sum(counts.values()), "failures": 0, "model_calls": 0,
        "default_db_unchanged": True, "runtime_stopped": True, "persistence": persistence,
        "rss_peak_mib": max(row["rss_bytes"] for row in resources) / 2**20,
        "health_max_ms": 1000 * max(row["health_seconds"] for row in resources),
        "scope": plan["scope"], "limitations": "Synthetic local workload, no real-model concurrency or production SLA"}
    stages_path = directory / "quiz-stages.jsonl"
    if stages_path.exists():
        rows = [json.loads(line) for line in stages_path.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == counts["quiz/answer"] and all(row["status"] == "completed" for row in rows)
        answer["quiz_internal_max_ms"] = 1000 * max(row["total_seconds"] for row in rows)
        answer["retrieval_max_ms"] = 1000 * max(stage["seconds"] for row in rows for stage in row["stages"]
                                                 if stage["stage"] == "related_retrieval")
        if (directory / "retriever-init.json").exists():
            init = load("retriever-init.json")
            assert init["ended_monotonic"] < min(row["started_monotonic"] for row in rows)
            answer["init_before_first_quiz"] = True
            answer["init_seconds"] = init["seconds"]
    if plan["seconds"] == 180:
        # The 180-second plan freezes this specific diagnostic bound before dispatch.
        assert answer["requests"] >= 200
        first, last = resources[0]["time_monotonic"], resources[-1]["time_monotonic"]
        early = [row["rss_bytes"] for row in resources if first + 10 <= row["time_monotonic"] <= first + 30]
        late = [row["rss_bytes"] for row in resources if row["time_monotonic"] >= last - 30]
        growth = (statistics.median(late) - statistics.median(early)) / 2**20
        assert growth < 64
        answer["rss_late_minus_early_median_mib"] = growth
        answer["rss_diagnostic_bound_mib"] = 64
    return answer


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = audit(args.directory)
    write_once(args.directory / "verification.json", result)
    print(json.dumps(result, ensure_ascii=False))
