"""Offline evidence checks for public HTTP quality preflights, not semantic gold."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from agents.sources import source_preview
from scripts.audit_flash_answers import citation_checks


def audit(packet, run):
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    manifest = read(packet / "manifest.json")
    for name, digest in manifest["frozen_sha256"].items():
        if hashlib.sha256((packet / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Frozen input changed")
    if hashlib.sha256((run / "executor.py").read_bytes()).hexdigest() != manifest["executor_sha256"]:
        raise ValueError("Executed script snapshot changed")
    cases, rows = read(packet / "cases.json"), read(run / "results.json")
    frozen_previews = [source_preview(chunk) for chunk in read(packet / "chunks.json")]
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("HTTP audit requires nonempty unique cases")
    indexed = {row["id"]: row for row in rows}
    if len(indexed) != len(rows) or set(indexed) - {case["id"] for case in cases}:
        raise ValueError("Duplicate or unknown HTTP result IDs")
    checks, rounds = [], {}
    for case in cases:
        rounds[case["thread_group"]] = rounds.get(case["thread_group"], 0) + 1
        row = indexed.get(case["id"])
        check = {"id": case["id"], "passed": False}
        if row is not None:
            events, trace = row["events"], row["trace"]
            contexts = next((event["contexts"] for event in events if event["type"] == "contexts"), [])
            answer = "".join(event.get("content", "") for event in events if event["type"] == "token")
            observations = trace["trace"]["events"]
            expected_intent = case.get("expected_intent", "knowledge")
            if expected_intent not in {"knowledge", "quiz", "chitchat"}:
                raise ValueError("Unknown expected intent")
            detail = {"input_matches": row["question"] == case["question"],
                      "intent_matches": any(e["type"] == "intent" and e.get("intent") == expected_intent for e in events),
                      "sse_done": bool(events) and events[-1]["type"] == "done"
                      and not any(event["type"] == "error" for event in events),
                      "history_pairs": row["history_rows"] == rounds[case["thread_group"]] * 2,
                      "durable_trace": trace["status"] == "completed"
                      and trace["trace"]["conversation_commit"] == "confirmed",
                      "no_degraded_stage": not any("degraded" in event["outcome"] for event in observations)}
            detail["frozen_source_previews"] = all(context in frozen_previews for context in contexts)
            if expected_intent != "knowledge":
                stage = "quiz_hint" if expected_intent == "quiz" else "chitchat"
                detail["nonknowledge_path"] = not contexts and bool(answer) and any(
                    event["stage"] == stage and event["outcome"] == "completed" for event in observations)
            elif case["target_locator"] is None:
                detail["scoped_abstention"] = not contexts and bool(answer) and any(
                    event["stage"] == "answer" and event["outcome"] == "abstained_no_context" for event in observations)
            else:
                detail["target_retrieved"] = case["target_locator"] in [c.get("locator") for c in contexts]
                detail["citation_structure"] = not citation_checks(answer, contexts)["errors"]
                if rounds[case["thread_group"]] > 1:
                    detail["rewrite_observed"] = any(call.get("stage") == "rewrite" and call["status"] == "completed"
                                                       for call in trace["trace"]["model_calls"])
            check.update(passed=all(detail.values()), checks=detail)
        checks.append(check)
    summary, database = read(run / "summary.json"), read(run / "default-db.json")
    receipts = [json.loads(line) for line in (run / "receipts.jsonl").read_text(encoding="utf-8").splitlines()]
    valid_usage = all(type(receipt.get("usage", {}).get(key)) is int and receipt["usage"][key] >= 0
                      for receipt in receipts for key in ("prompt_tokens", "completion_tokens"))
    bounded_usage = valid_usage and all(receipt["usage"]["prompt_tokens"] <= 5500
                                        and receipt["usage"]["completion_tokens"] <= manifest["max_output_tokens"]
                                        for receipt in receipts)
    recomputed_cost = sum((receipt["usage"]["prompt_tokens"] * .3
                           + receipt["usage"]["completion_tokens"] * 1.2) / 1e6
                          for receipt in receipts) if valid_usage else None
    receipt_ids = [receipt["run_id"] for receipt in receipts]
    observed_calls = [call for row in rows for call in row["trace"]["trace"]["model_calls"]]
    matching_receipts = (len(set(receipt_ids)) == len(receipt_ids)
                         and len(observed_calls) == len(receipts)
                         and all(call.get("stage") == receipt["stage"] and call["usage"] == receipt["usage"]
                                 for call, receipt in zip(observed_calls, receipts, strict=True)))
    streamed_answers = ["".join(event.get("content", "") for event in row["events"] if event["type"] == "token")
                        for row in rows if any(call.get("stage") == "answer"
                                               for call in row["trace"]["trace"]["model_calls"])]
    provider_answers = [receipt["response"]["generations"][0][0]["message"]["content"]
                        for receipt in receipts if receipt["stage"] == "answer"]
    answers_match = streamed_answers == provider_answers
    budget_ok = (not summary["cost_unknown"] and not summary["stopped"] and bounded_usage
                 and matching_receipts and answers_match
                 and len(receipts) == summary["dispatch_attempts"] <= manifest["max_calls"]
                 and math.isclose(summary["known_cost_estimate_usd"], recomputed_cost, rel_tol=1e-9, abs_tol=1e-12)
                 and summary["known_cost_estimate_usd"] <= manifest["budget_usd"])
    unchanged = database["before"] == database["after"] and database["unchanged"]
    execution_complete = (summary.get("status", "completed") == "completed"
                          and summary["completed_http_requests"] == len(cases)
                          and summary.get("recorded_http_requests", len(rows)) == len(rows))
    return {"passed": all(row["passed"] for row in checks) and budget_ok and unchanged and execution_complete,
            "checks": checks, "budget_receipts_valid": budget_ok, "default_database_unchanged": unchanged,
            "execution_complete": execution_complete,
            "semantic_source_review": "required separately", "independent_human_gold": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("packet", "run", "output"):
        parser.add_argument("--" + option, type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.packet, args.run)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"passed": result["passed"]}))
    if not result["passed"]:
        raise SystemExit(1)
