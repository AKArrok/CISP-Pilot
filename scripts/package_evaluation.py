"""Package frozen public HTTP evaluation artifacts for the workspace; never run a model."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storage.evaluation_store import Bundle  # noqa: E402


def package(packet, run, schema_version=1, dataset_key="public-nist-http"):
    artifacts = {}

    def read(directory, name):
        raw = (directory / name).read_bytes()
        artifacts[("packet/" if directory == packet else "run/") + name] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    manifest = read(packet, "manifest.json")
    for name, expected in manifest["frozen_sha256"].items():
        candidate = (packet / name).resolve()
        if not candidate.is_relative_to(packet.resolve()) or hashlib.sha256(candidate.read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen input missing or changed")
    cases, rows = read(packet, "cases.json"), read(run, "results.json")
    audit, summary = read(run, "audit.json"), read(run, "summary.json")
    semantic = read(run, "semantic-review.json") if (run / "semantic-review.json").exists() else {}
    reviews = {row["id"]: row for row in semantic.get("cases", [])}
    checks = {row["id"]: row for row in audit["checks"]}
    if len(reviews) != len(semantic.get("cases", [])) or len(checks) != len(audit["checks"]):
        raise ValueError("Duplicate review/audit IDs")
    results = []
    case_questions = {case["id"]: case["question"] for case in cases}
    for row in rows:
        if row.get("question") != case_questions.get(row["id"]):
            raise ValueError("Recorded question differs from frozen input")
        answer = "".join(event.get("content", "") for event in row["events"] if event["type"] == "token")
        answer_hash = hashlib.sha256(answer.encode()).hexdigest()
        review = reviews.get(row["id"], {})
        if review and review.get("answer_sha256") != answer_hash:
            raise ValueError("Saved review does not bind to this answer")
        result_checks = dict(checks.get(row["id"], {}).get("checks", {}))
        if row["id"] in checks:
            result_checks["recorded_execution_audit"] = checks[row["id"]]["passed"]
        results.append({"id": row["id"], "answer": answer, "answer_sha256": answer_hash,
                        "contexts": next((event["contexts"] for event in row["events"] if event["type"] == "contexts"), []),
                        "checks": result_checks, "semantic_passed": review.get("source_supported"),
                        "semantic_reviewer": semantic.get("reviewer", "") if review else "",
                        "semantic_reason": review.get("note", "")})
    ids = {case["id"] for case in cases}
    if set(checks) - ids or set(reviews) - ids:
        raise ValueError("Unknown audit/review IDs")
    data = {"schema_version": 1, "name": run.name, "dataset_name": "NIST public HTTP / 40-case development",
            "role": "development", "visibility": "public", "criteria": manifest["acceptance"],
            "config": {key: manifest[key] for key in ("model", "thinking", "max_output_tokens", "answer_temperature",
                       "retrieval_k", "dense", "answer_strategy", "base_commit", "answer_card_protocol") if key in manifest},
            "artifacts": artifacts, "cases": [{"id": case["id"], "question": case["question"],
                                               "contract": {key: value for key, value in case.items() if key not in {"id", "question"}}}
                                              for case in cases],
            "results": results, "known_cost_usd": summary.get("known_cost_estimate_usd"),
            "cost_unknown": summary.get("cost_unknown", True)}
    if schema_version == 1:
        return Bundle.model_validate(data)
    from storage.evaluation_workspace import parse_bundle

    originals = {row["id"]: row for row in rows}
    data.update(schema_version=2, dataset_key=dataset_key,
                version_label=packet.name, repetitions=1)
    if manifest.get("citation_layout"):
        data["config"]["citation_layout"] = manifest["citation_layout"]
    override = manifest.get("fallback_system_override", False)
    if type(override) is not bool:
        raise ValueError("Frozen prompt override must be boolean")
    prompt_file = "fallback-system.json" if override else "prompts.json"
    if prompt_file in manifest["frozen_sha256"]:
        settings = read(packet, prompt_file)
        system = settings.get("system" if override else "ANSWER_SYSTEM")
        if not isinstance(system, str) or not system.strip():
            raise ValueError("Frozen answer system must be nonempty text")
        data["config"].update(answer_system_sha256=hashlib.sha256(system.encode()).hexdigest(),
                              fallback_system_override=override)
    elif override:
        raise ValueError("Prompt override must bind a frozen system file")
    if "chunks.json" in manifest["frozen_sha256"]:
        # This exact file was verified above, unlike an inferred source-name match.
        data["artifacts"]["knowledge:" + dataset_key] = manifest["frozen_sha256"]["chunks.json"]
    conversations = {}
    for case in data["cases"]:
        group = case["contract"].get("thread_group", case["id"])
        preceding = conversations.setdefault(group, [])
        case["contract"] = case["contract"] | {"preceding_user_questions": list(preceding)}
        preceding.append(case["question"])
    for result in data["results"]:
        original = originals[result["id"]]
        trace = original.get("trace", {})
        result.update(attempt_no=1, trace=trace, events=original["events"], request_id=trace.get("request_id"),
                      latency_ms=original["elapsed_seconds"] * 1000 if original.get("elapsed_seconds") is not None else None)
        details = trace.get("trace", {})
        calls = details.get("model_calls", [])
        if calls and details.get("dropped_model_calls") == 0 and all(call.get("response_observed") is True for call in calls) and all(
                type(call.get("usage", {}).get(key)) is int and call["usage"][key] >= 0
                for call in calls for key in ("prompt_tokens", "completion_tokens")):
            for key in ("prompt_tokens", "completion_tokens"):
                result[key] = sum(call["usage"][key] for call in calls)
    return parse_bundle(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--schema-version", type=int, choices=(1, 2), default=1)
    parser.add_argument("--dataset-key", default="public-nist-http", help="Stable logical dataset identity for schema 2")
    parser.add_argument("--confirm-public", action="store_true", required=True,
                        help="Confirm the selected packet/run contain only approved public evidence")
    args = parser.parse_args()
    bundle = package(args.packet, args.run, args.schema_version, args.dataset_key)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(bundle.model_dump_json(indent=2))
    print(json.dumps({"output": str(args.output), "cases": len(bundle.cases), "model_calls": 0}))


if __name__ == "__main__":
    main()
