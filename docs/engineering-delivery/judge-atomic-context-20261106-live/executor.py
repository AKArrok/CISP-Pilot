"""Frozen public-NIST development evaluation: offline retrieval or approved live run."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from retrieval.bm25 import BM25Index  # noqa: E402
from scripts.answer_grade_contract import line_payload  # noqa: E402


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def request_content(stage, case, row, context, manifest):
    if stage.startswith("judge") and manifest.get("evidence_protocol") == "atomic_lines":
        return json.dumps({"target_fragment": row["answer"], "answer_context": case["fragment_context"],
                           "contexts": line_payload(row)["contexts"]}, ensure_ascii=False)
    if stage.startswith("judge") and manifest.get("evidence_protocol") == "line_ids":
        payload = line_payload(row)
        if manifest.get("claim_focus"):
            payload["focus_claims"] = [line for line in payload["answer_lines"]
                                       if re.search(r"即使|自行|禁止|不得|只有|唯一", line["text"])]
        if manifest.get("claim_only"):
            payload["answer_lines"] = payload.get("focus_claims") or payload["answer_lines"]
            payload.pop("question")
        return json.dumps(payload, ensure_ascii=False)
    return context + "\n问题：" + case["question"] + ("\n待审回答：" + row["answer"] if stage.startswith("judge") else "")


def select_retrieval_arm(metrics, configured=None):
    if configured is not None:
        if configured not in metrics:
            raise ValueError("Unknown frozen retrieval arm")
        return configured
    return max(metrics, key=lambda a: (metrics[a]["top1"], metrics[a]["top4"], a == "bilingual")) if metrics else None


def input_token_limit(manifest):
    limit = manifest.get("max_estimated_input_tokens", 5000)
    if type(limit) is not int or not 1 <= limit <= 5500:
        raise ValueError("Estimated input limit must be an integer from 1 to 5500")
    return limit


def record_failure(output, summary, work, completed, current, error):
    summary["stop_reason"] = type(error).__name__
    summary["known_cost_lower_bound_usd"] = summary["cost_usd_conservative"]
    if summary.get("cost_unknown"):
        summary["cost_usd_conservative"] = None
    done = {item["id"] for item in completed}
    statuses = [{"id": item["id"], "status": "completed" if item["id"] in done else
                 "failed" if item["id"] == current else "not_started_after_failure"} for item in work]
    write(output / "execution-status.json", statuses)
    write(output / "failure.json", {"id": current, "error_type": type(error).__name__,
                                   "http_status": getattr(error, "status_code", None),
                                   "request_id": getattr(error, "request_id", None)})
    write(output / "summary.json", summary)


def retrieve(cases, chunks):
    arms = {"source_only": [{k: v for k, v in c.items() if k != "embedding_text"} for c in chunks],
            "bilingual": chunks,
            "source_plus_alias": [{**c, "embedding_text": c["text"] + "\n" + c["embedding_text"]} for c in chunks]}
    rows = []
    for arm, corpus in arms.items():
        index = BM25Index.build(corpus)
        for case in cases:
            started = time.monotonic()
            hits = index.search(case["question"], 4)
            rows.append({"id": case["id"], "arm": arm,
                         "target_rank": next((i+1 for i, c in enumerate(hits) if c["control_id"] == case["target_control"]), None),
                         "locators": [c["locator"] for c in hits],
                         "elapsed_seconds": time.monotonic()-started})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, default=ROOT / "evaluation/nist-development-v1")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    manifest = load(args.packet / "manifest.json")
    for flag in ("claim_focus", "claim_only"):
        if type(manifest.get(flag, False)) is not bool:
            raise ValueError(f"{flag} must be an explicitly frozen boolean")
    if ("executor_sha256" in manifest
            and hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != manifest["executor_sha256"]):
        raise ValueError("Frozen executor changed")
    max_input_tokens = input_token_limit(manifest)
    for name, digest in manifest["frozen_sha256"].items():
        if hashlib.sha256((args.packet / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Frozen input changed")
    cases, chunks = load(args.packet / "cases.json"), load(args.packet / "chunks.json")
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "executor.py").write_bytes(Path(__file__).read_bytes())
    calibration = manifest.get("mode") == "judge_calibration"
    rows = [] if calibration else retrieve(cases, chunks)
    write(args.output / "retrieval.json", rows)
    metrics = {arm: {"top1": sum(r["target_rank"] == 1 for r in rows if r["arm"] == arm),
                     "top4": sum(r["target_rank"] is not None for r in rows if r["arm"] == arm)}
               for arm in sorted({r["arm"] for r in rows})}
    # Maximize top1 then top4; prefer the established bilingual index on ties.
    selected = select_retrieval_arm(metrics, manifest.get("retrieval_arm"))
    summary = {"cases": len(cases), "retrieval": metrics, "selected_arm": selected,
               "provider_calls": 0, "cost_usd_conservative": 0, "semantic_acceptance": "pending_source_review"}
    write(args.output / "summary.json", summary)
    if not args.execute:
        print(json.dumps(summary))
        return
    authorization = load(args.packet / "authorization.json")
    max_calls, budget = manifest["paid_proposal"]["calls"], manifest["paid_proposal"]["budget_usd"]
    if authorization != {"max_calls": max_calls, "budget_usd": budget,
                          "manifest_sha256": hashlib.sha256((args.packet / "manifest.json").read_bytes()).hexdigest()}:
        raise ValueError("Authorization mismatch")
    with (args.packet / "dispatch.lock").open("x", encoding="utf-8") as stream:
        stream.write("One approved batch; never remove to retry consumed calls.\n")
    from dotenv import dotenv_values
    from openai import OpenAI
    import tiktoken
    import truststore
    truststore.inject_into_ssl()
    env = dotenv_values(ROOT / ".env", interpolate=False)
    base = os.environ.get("LLM_BASE_URL") or env.get("LLM_BASE_URL") or "https://api.deepseek.com/v1"
    url = urlsplit(base)
    if url.scheme != "https" or url.hostname != "api.deepseek.com" or url.username or url.password or url.query or url.fragment or url.port not in (None, 443):
        raise ValueError("Unapproved provider endpoint")
    client = OpenAI(api_key=os.environ.get("LLM_API_KEY") or env.get("LLM_API_KEY"), base_url=base, max_retries=0)
    encoding = tiktoken.get_encoding("cl100k_base")
    prompts = load(args.packet / "prompts.json")
    if calibration:
        work = cases
    else:
        regression = load(args.packet / "at2-regression.json")
        work = [{"id": "at2-regression", "question": regression["question"], "fixed_contexts": regression["contexts"]}] + cases
    outputs = []
    try:
        for case in work:
            row = {"id": case["id"], "question": case["question"], "status": "in_progress"}
            if "fixed_contexts" in case:
                contexts = case["fixed_contexts"]
            else:
                match = next(r for r in rows if r["arm"] == selected and r["id"] == case["id"])
                contexts = [next(c for c in chunks if c["locator"] == loc) for loc in match["locators"][:2]]
            row["contexts"] = [{k: c[k] for k in ("source", "locator", "source_url", "text")} for c in contexts]
            if calibration:
                row["answer"] = case["answer"]
            context = "\n".join(f'[资料{i+1}]《{c["source"]}》定位【{c["locator"]}】\n{c["text"]}' for i, c in enumerate(contexts))
            for stage in (manifest.get("judge_arms", ["judge_baseline", "judge_candidate"]) if calibration else ("answer", "judge")):
                content = request_content(stage, case, row, context, manifest)
                messages = [{"role": "system", "content": prompts[stage + "_system"]},
                            {"role": "user", "content": content}]
                estimate = sum(len(encoding.encode(m["content"])) for m in messages)
                if estimate > max_input_tokens or summary["provider_calls"] >= max_calls or summary["cost_usd_conservative"] + .008 > budget:
                    raise RuntimeError("Input or budget limit; no automatic retry")
                summary["provider_calls"] += 1
                summary["cost_unknown"] = True
                summary["pending_call"] = {"id": case["id"], "stage": stage}
                write(args.output / "summary.json", summary)
                started = time.monotonic()
                response = client.chat.completions.create(model="deepseek-flash", messages=messages,
                    max_tokens=1024, temperature=0, timeout=60, extra_body={"thinking": {"type": "disabled"}},
                    **({"response_format": {"type": "json_object"}} if stage.startswith("judge") else {}))
                receipt = {"id": case["id"], "stage": stage, "response": response.model_dump(),
                           "elapsed_seconds": time.monotonic()-started}
                with (args.output / "receipts.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(receipt, ensure_ascii=False) + "\n")
                usage = response.usage
                if usage is None or usage.prompt_tokens is None or usage.completion_tokens is None:
                    summary["cost_unknown"] = True
                    raise RuntimeError("Unknown usage")
                summary["cost_usd_conservative"] += (usage.prompt_tokens*.3 + usage.completion_tokens*1.2)/1e6
                summary["cost_unknown"] = False
                summary.pop("pending_call", None)
                if usage.prompt_tokens > 6000 or usage.completion_tokens > 1024 or summary["cost_usd_conservative"] > budget:
                    raise RuntimeError("Actual input, output or cost exceeded bound")
                choice = response.choices[0]
                if choice.finish_reason != "stop" or getattr(choice.message, "reasoning_content", None):
                    raise RuntimeError("Truncation or unexpected thinking; stop batch")
                row[stage] = choice.message.content
                write(args.output / "partial.json", row)
            row["status"] = "completed"
            outputs.append(row)
            write(args.output / "results.json", outputs)
            write(args.output / "summary.json", summary)
            print(case["id"] + ": completed", flush=True)
    except Exception as exc:
        record_failure(args.output, summary, work, outputs, case["id"], exc)
        raise
    summary["completed"] = len(outputs)
    write(args.output / "summary.json", summary)


if __name__ == "__main__":
    main()
