"""Three-case DeepSeek Flash comparison with fixed, bounded course contexts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agents.usage import service_receipt  # noqa: E402
from scripts.run_rag_answer_comparison import PREPARED, messages, preflight  # noqa: E402

IDS = ("continuity_event_levels", "continuity_incident_process", "assurance_ppdr")
ARMS = ("sparse_without_features", "sparse_with_features")
MAX_CALLS = 18
MAX_OUTPUT = 8192
MAX_INPUT = 6000


def prepare(output, system_prompt=None):
    _, _, rows, plan_hash = preflight(PREPARED, output)
    selected = {(row["case_id"], row["arm"]): row for row in rows if row["case_id"] in IDS}
    if set(selected) != {(case, arm) for case in IDS for arm in ARMS}:
        raise ValueError("Unexpected selected rows")
    # cl100k is only an estimate; actual usage is checked and an overage stops the run.
    import tiktoken
    encoding = tiktoken.get_encoding("cl100k_base")
    prepared = []
    for case in IDS:
        for arm in ARMS:
            row = selected[(case, arm)]
            contexts, payload = messages(row)
            if system_prompt is not None:
                payload[0]["content"] = system_prompt
            estimate = sum(len(encoding.encode(item["content"], disallowed_special=())) for item in payload) + 32
            if estimate > 5000:
                raise ValueError("Estimated input exceeds safety margin")
            prepared.append({"case_id": case, "arm": arm, "contexts": contexts,
                             "messages": payload, "estimated_input_tokens": estimate})
    return prepared, plan_hash


def run(prepared, invoke, receipts, clock=time.monotonic):
    dispatches = 0
    spent = 0.0
    stopped = None
    cost_unknown = False
    deadline = clock() + 1200
    for trial in range(1, 4):
        for item in prepared:
            record = {"case_id": item["case_id"], "arm": item["arm"], "trial": trial,
                      "status": "skipped", "usage": None, "answer": None,
                      "estimated_input_tokens": item["estimated_input_tokens"]}
            remaining = deadline - clock()
            if not stopped and remaining > 0 and dispatches < MAX_CALLS and spent + 0.012 < 1:
                start = clock()
                try:
                    dispatches += 1
                    response = invoke(item["messages"], min(60, remaining))
                    usage = service_receipt(response)
                    record.update(usage=usage, response_model=getattr(response, "model", None))
                    prompt, completion = usage["prompt_tokens"], usage["completion_tokens"]
                    if prompt is None or completion is None:
                        raise ValueError("UnknownUsage")
                    spent += (prompt * 0.3 + completion * 1.2) / 1_000_000
                    if prompt > MAX_INPUT or completion > MAX_OUTPUT or spent >= 1:
                        raise ValueError("BudgetExceeded")
                    choice = response.choices[0]
                    record["finish_reason"] = choice.finish_reason
                    record["answer"] = choice.message.content
                    if choice.finish_reason != "stop" or not isinstance(record["answer"], str) or not record["answer"].strip():
                        raise ValueError("InvalidAnswer")
                    record["status"] = "valid"
                except Exception as exc:
                    record.update(status="invalid", error_type=type(exc).__name__)
                    if record["usage"] is None or record["usage"]["prompt_tokens"] is None or record["usage"]["completion_tokens"] is None:
                        cost_unknown = True
                    stopped = "invalid_or_unknown_usage"
                record["elapsed_seconds"] = round(clock() - start, 3)
            else:
                stopped = stopped or "budget_or_deadline"
            record["stop_reason"] = stopped
            receipts.write(json.dumps(record, ensure_ascii=False) + "\n")
            receipts.flush()
            os.fsync(receipts.fileno())
    return {"dispatches": dispatches, "conservative_cost_usd": None if cost_unknown else round(spent, 6),
            "observed_cost_usd_lower_bound": round(spent, 6), "stop_reason": stopped}


def make_client():
    """Create the approved HTTPS client, with SDK retries disabled."""
    from dotenv import dotenv_values
    from openai import OpenAI
    from urllib.parse import urlsplit
    import truststore
    truststore.inject_into_ssl()
    values = dotenv_values(ROOT / ".env", interpolate=False)
    key = os.environ.get("LLM_API_KEY") or values.get("LLM_API_KEY")
    base = os.environ.get("LLM_BASE_URL") or values.get("LLM_BASE_URL") or "https://api.deepseek.com/v1"
    parsed = urlsplit(base)
    if (parsed.scheme != "https" or parsed.hostname != "api.deepseek.com" or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError("Only approved DeepSeek HTTPS endpoint allowed")
    return OpenAI(api_key=key, base_url=base, max_retries=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--system-prompt-file", type=Path)
    args = parser.parse_args()
    system_prompt = args.system_prompt_file.read_text(encoding="utf-8") if args.system_prompt_file else None
    prepared, plan_hash = prepare(args.output_dir, system_prompt)
    metadata = {"cases": list(IDS), "arms": list(ARMS), "trials": 3, "max_calls": MAX_CALLS,
                "model": "deepseek-flash", "thinking": {"type": "disabled"},
                "max_output_tokens": MAX_OUTPUT, "max_input_tokens_observed": MAX_INPUT,
                "estimated_input_tokens": [item["estimated_input_tokens"] for item in prepared],
                "original_plan_sha256": plan_hash, "inputs_sha256": hashlib.sha256(
                    (PREPARED / "answer-inputs.json").read_bytes()).hexdigest(),
                "human_authorization": "user requested the previously proposed small comparison",
                "dataset_role": "development", "human_quality": "not_reviewed"}
    metadata["system_prompt_sha256"] = hashlib.sha256(prepared[0]["messages"][0]["content"].encode()).hexdigest()
    if not args.execute:
        print(json.dumps(metadata, ensure_ascii=False))
        return
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "plan.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    client = make_client()

    def invoke(payload, timeout):
        return client.chat.completions.create(model="deepseek-flash", messages=payload, temperature=0.3,
                                              max_tokens=MAX_OUTPUT, timeout=timeout,
                                              extra_body={"thinking": {"type": "disabled"}})

    with (args.output_dir / "receipts.jsonl").open("x", encoding="utf-8") as receipts:
        summary = run(prepared, invoke, receipts)
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
