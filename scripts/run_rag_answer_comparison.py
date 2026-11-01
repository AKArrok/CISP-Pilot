"""Preflight by default; execution requires a plan-bound operator confirmation.

That confirmation does not establish human permission to transmit course material.
No resume: every execution uses a new directory and at most one dispatch per row.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agents.prompts import ANSWER_CONTEXT_TEMPLATE, ANSWER_SYSTEM  # noqa: E402
from agents.usage import service_receipt  # noqa: E402

PREPARED = ROOT / "docs/engineering-delivery/rag-answer-prepared-20261010"
ARMS = {"sparse_without_features", "sparse_with_features"}
BUDGET = {"model": "deepseek-v4-flash", "calls_max": 40, "attempts_per_answer": 1,
          "output_tokens_per_answer_max": 8192, "timeout_seconds_per_answer": 60,
          "run_seconds_max": 2400, "context_parents_max": 5, "context_characters_max": 6000}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def preflight(prepared, output, approved=None):
    if output.exists():
        raise FileExistsError("Output directory already exists; resume/overwrite forbidden")
    plan = read(prepared / "answer-run-plan.json")
    bindings = {"prepared_cases_sha256": prepared / "cases.json",
                "contexts_sha256": prepared / "answer-inputs.json",
                "preparer_sha256": ROOT / "scripts/prepare_rag_answer_run.py",
                "source_frozen_cases_sha256": ROOT / "docs/engineering-delivery/rag-development-20261007-verified/cases.json"}
    if any(plan.get(key) != sha(path) for key, path in bindings.items()):
        raise ValueError("Prepared input/source hash mismatch")
    plan_hash = sha(prepared / "answer-run-plan.json")
    if approved is not None and approved != plan_hash:
        raise ValueError("Operator confirmation must match current plan SHA256")
    if (plan.get("dataset_role") != "development"
            or plan.get("prepared_cases_sha256") != plan.get("source_frozen_cases_sha256")
            or plan.get("proposed_answer_budget") != BUDGET or plan.get("cases") != 20
            or plan.get("arms") != 2 or plan.get("answers_planned") != 40
            or plan.get("provider_calls_during_preparation") != 0):
        raise ValueError("Unexpected fixed answer plan")
    dataset, rows = read(prepared / "cases.json"), read(prepared / "answer-inputs.json")
    cases = dataset["cases"]
    questions = {case["id"]: case["question"] for case in cases}
    if dataset.get("dataset_role") != "development" or len(cases) != 20 or len(questions) != 20:
        raise ValueError("Expected twenty unique development cases")
    seen = set()
    for row in rows:
        pair = (row["case_id"], row["arm"])
        contexts = row["contexts"]
        if (pair in seen or row["arm"] not in ARMS or row["case_id"] not in questions
                or row["question"] != questions[row["case_id"]]
                or row.get("answer_status") != "not_generated"
                or row.get("human_quality") != "not_reviewed"):
            raise ValueError("Unexpected/duplicate answer row or reviewed input")
        if (len(contexts) > 5 or len({c["parent_id"] for c in contexts}) != len(contexts)
                or any(not isinstance(c["text"], str) or not c.get("source") or c.get("page") is None for c in contexts)
                or sum(len(c["text"]) for c in contexts) > 6000
                or row["context_characters"] != sum(len(c["text"]) for c in contexts)):
            raise ValueError("Invalid bounded source contexts")
        seen.add(pair)
    if seen != {(case_id, arm) for case_id in questions for arm in ARMS} or len(rows) != 40:
        raise ValueError("Expected forty paired answer rows")
    return plan, dataset, rows, plan_hash


def messages(row):
    # Only these three course fields enter the provider payload; no gold or metadata.
    contexts = [{key: c[key] for key in ("source", "page", "text")} for c in row["contexts"]]
    chunks = "\n".join(f'<chunk id="资料{i}">{json.dumps(c, ensure_ascii=False)}</chunk>'
                       for i, c in enumerate(contexts, 1))
    return contexts, [{"role": "system", "content": ANSWER_SYSTEM},
                      {"role": "user", "content": ANSWER_CONTEXT_TEMPLATE.format(contexts=chunks, question=row["question"])}]


def provider():
    # Read only explicitly allowed dotenv settings, only after execution checks.
    from dotenv import dotenv_values
    values = dotenv_values(ROOT / ".env", interpolate=False)
    settings = {key: os.environ.get(key) or values.get(key) for key in ("LLM_API_KEY", "LLM_BASE_URL")}
    base_url = settings["LLM_BASE_URL"] or "https://api.deepseek.com/v1"
    parsed = urlsplit(base_url)
    if (parsed.scheme != "https" or parsed.hostname != "api.deepseek.com"
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError("Only the approved HTTPS DeepSeek endpoint is allowed")
    from openai import OpenAI
    client = OpenAI(api_key=settings["LLM_API_KEY"],
                    base_url=base_url, max_retries=0)

    def invoke(payload, timeout):
        return client.chat.completions.create(model=BUDGET["model"], messages=payload,
                                              temperature=0.3, max_tokens=8192, timeout=timeout)
    return invoke


def execute(prepared, output, approved, *, invoke=None, clock=time.monotonic):
    if not approved:
        raise ValueError("Execution requires --approved-plan-sha256 operator confirmation")
    plan, dataset, rows, plan_hash = preflight(prepared, output, approved)
    output.mkdir(parents=True, exist_ok=False)
    review_cases = {**dataset, "source_frozen_cases_sha256": plan["prepared_cases_sha256"],
                    "requirement_policy": "Original-question completeness rubric; not atomic gold or human labels",
                    "cases": [{**case, "requirements": [{"id": "question-completeness", "question": case["question"]}]}
                              for case in dataset["cases"]]}
    save(output / "review-cases.json", review_cases)
    comparison = {"dataset_role": "development", "dataset_sha256": sha(output / "review-cases.json"),
                  "source_frozen_cases_sha256": plan["prepared_cases_sha256"], "inputs_sha256": plan["contexts_sha256"],
                  "plan_sha256": plan_hash, "operator_confirmed_plan_sha256": approved,
                  "prepared_execution_allowed": plan.get("execution_allowed"),
                  "human_authorization": "not_established_by_cli_confirmation", "provider_dispatches": 0,
                  "cases": [{"id": case["id"], "arms": []} for case in dataset["cases"]]}
    by_id = {case["id"]: case for case in comparison["cases"]}
    deadline = clock() + 2400
    with (output / "answer-receipts.jsonl").open("x", encoding="utf-8") as receipts:
        for row in rows:
            contexts, payload = messages(row)
            result = {"name": row["arm"], "status": "skipped_budget", "answer": None, "contexts": contexts,
                      "human_quality": "not_reviewed", "usage": service_receipt(None), "attempts": 0,
                      "response_observed": False, "response_model": None, "finish_reason": None,
                      "partial_answer": None}
            remaining = deadline - clock()
            if remaining > 0 and comparison["provider_dispatches"] < 40:
                started = clock()
                try:
                    if invoke is None:
                        invoke = provider()
                    remaining = deadline - clock()
                    timeout = min(60, remaining, 60 - (clock() - started))
                    if timeout <= 0:
                        raise TimeoutError()
                    # No application retry and SDK retries=0: one dispatch only.
                    result["attempts"] = 1
                    comparison["provider_dispatches"] += 1
                    response = invoke(payload, timeout)
                    result.update(response_observed=True, response_model=getattr(response, "model", None))
                    result["usage"] = service_receipt(response)
                    choice = response.choices[0]
                    answer = choice.message.content
                    result.update(finish_reason=choice.finish_reason,
                                  partial_answer=answer if isinstance(answer, str) else None)
                    if clock() >= deadline or clock() - started >= 60:
                        raise TimeoutError()
                    if choice.finish_reason != "stop" or (result["usage"]["completion_tokens"] or 0) > 8192:
                        raise ValueError("OutputLimit")
                    if not isinstance(answer, str) or not answer.strip():
                        raise ValueError("MissingAnswer")
                    result.update(status="valid", answer=answer)
                except Exception as error:
                    result.update(status="invalid", error_type=type(error).__name__)
            result["elapsed_seconds"] = max(0, clock() - started) if result["attempts"] else 0
            by_id[row["case_id"]]["arms"].append(result)
            receipts.write(json.dumps({"case_id": row["case_id"], **result}, ensure_ascii=False) + "\n")
            receipts.flush()
            os.fsync(receipts.fileno())
    save(output / "comparison.json", comparison)
    return comparison


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-dir", type=Path, default=PREPARED)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approved-plan-sha256")
    args = parser.parse_args()
    if args.execute:
        result = execute(args.prepared_dir, args.output_dir, args.approved_plan_sha256)
        print(json.dumps({"provider_dispatches": result["provider_dispatches"],
                          "comparison": str(args.output_dir / "comparison.json"),
                          "review_cases": str(args.output_dir / "review-cases.json")}))
        if any(arm["status"] != "valid" for case in result["cases"] for arm in case["arms"]):
            raise SystemExit(1)
    else:
        _, _, rows, digest = preflight(args.prepared_dir, args.output_dir)
        print(json.dumps({"preflight": "passed", "answers_planned": len(rows), "provider_dispatches": 0,
                          "plan_sha256": digest, "human_authorization": "not_established"}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"error_type": type(error).__name__}), file=sys.stderr)
        raise SystemExit(1) from None
