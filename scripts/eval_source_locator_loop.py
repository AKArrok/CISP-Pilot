"""Isolated public-NIST retrieval/answer contract replay or approved 16-call validation."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time
from unittest.mock import patch

from langchain_core.messages import AIMessage

import config
from agents import graph
from agents.sources import source_preview
from agents.usage import service_receipt
from retrieval.bm25 import BM25Index
from retrieval.hybrid import HybridRetriever
from scripts.audit_flash_answers import citation_checks
from scripts.eval_answer_workflow import prompts, validate_grade
from scripts.eval_flash_small import make_client

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs/engineering-delivery/source-locator-loop-20261029"
CHUNKS = ROOT / "docs/engineering-delivery/supplemental-nist-bilingual-20261029-r2/bilingual-chunks.json"


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BoundedCalls:
    """One approved run, no retries; unknown usage stops all subsequent calls."""

    def __init__(self, client, output, *, max_calls=16, budget_usd=.10):
        import tiktoken
        self.client, self.output = client, output
        self.max_calls, self.budget_usd = max_calls, budget_usd
        self.encoding = tiktoken.get_encoding("cl100k_base")
        self.calls, self.spent, self.stopped, self.unknown = 0, 0.0, None, False
        self.deadline = time.monotonic() + 1200

    def invoke(self, messages, identifier, stage):
        estimate = 32 + sum(len(self.encoding.encode(m["content"], disallowed_special=())) for m in messages)
        if estimate > 5000:
            self.stopped = "InputEstimateExceeded"
        if self.stopped or self.calls >= self.max_calls or self.spent + .008 > self.budget_usd or time.monotonic() >= self.deadline:
            raise RuntimeError(self.stopped or "BudgetOrDeadline")
        row = {"id": identifier, "stage": stage, "status": "failed", "usage": None, "raw": None,
               "estimated_input_tokens": estimate}
        self.calls += 1
        started = time.monotonic()
        try:
            response = self.client.chat.completions.create(
                model="deepseek-flash", messages=messages, temperature=0, max_tokens=1024,
                timeout=min(60, self.deadline-started), extra_body={"thinking": {"type": "disabled"}},
                **({"response_format": {"type": "json_object"}} if stage == "judge" else {}))
            choice = response.choices[0]
            row.update(usage=service_receipt(response), raw=choice.message.content,
                       response_model=response.model, finish_reason=choice.finish_reason,
                       reasoning_content_present=bool(getattr(choice.message, "reasoning_content", None)))
            usage = row["usage"]
            if usage["prompt_tokens"] is None or usage["completion_tokens"] is None:
                self.unknown = True
                raise ValueError("UnknownUsage")
            self.spent += (usage["prompt_tokens"]*.3 + usage["completion_tokens"]*1.2)/1e6
            if (usage["prompt_tokens"] > 6000 or usage["completion_tokens"] > 1024 or self.spent > self.budget_usd
                    or row["reasoning_content_present"] or choice.finish_reason != "stop"
                    or not isinstance(row["raw"], str) or not row["raw"].strip()):
                raise ValueError("InvalidResponseOrBudget")
            row["status"] = "completed"
            return row["raw"]
        except Exception as exc:
            row["error_type"] = type(exc).__name__
            self.stopped = type(exc).__name__
            if row["usage"] is None:
                self.unknown = True
            raise
        finally:
            row["elapsed_seconds"] = round(time.monotonic()-started, 3)
            with (self.output / "provider-receipts.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()


async def execute(output, live=False):
    plan = json.loads((PLAN / "plan.json").read_text(encoding="utf-8"))
    for name, expected in plan["frozen_inputs"].items():
        if file_hash(ROOT / name) != expected:
            raise ValueError(f"Frozen input changed: {name}")
    if live:
        authorization = json.loads((PLAN / "authorization.json").read_text(encoding="utf-8"))
        if authorization["cases_sha256"] != file_hash(PLAN / "cases.json") or authorization["max_calls"] != 16 or authorization["budget_usd"] != .10:
            raise ValueError("Authorization does not match frozen packet")
    cases = json.loads((PLAN / "cases.json").read_text(encoding="utf-8"))
    chunks = json.loads(CHUNKS.read_text(encoding="utf-8"))
    if len(cases) != 8 or sum(c["kind"] == "unsupported" for c in cases) != 4:
        raise ValueError("Expected frozen eight-case packet")
    protected = {str(p): file_hash(p) for p in (ROOT / "data/cisp_qa.db", ROOT / "data/kb_chunks.json")}
    output.mkdir(exist_ok=False)
    write(output / "cases.json", cases)
    write(output / "chunks.json", chunks)
    (output / "executor.py").write_bytes(await asyncio.to_thread(Path(__file__).read_bytes))
    rubric = json.loads((ROOT / "docs/engineering-delivery/flash-answer-rubric-v1.json").read_text(encoding="utf-8"))
    manifest = {"mode": "live" if live else "offline_mock_contract", "public_sources_only": True,
                "model": "deepseek-flash", "thinking": "disabled", "max_calls": 16 if live else 0,
                "budget_usd": .10 if live else 0, "max_output_tokens_per_call": 1024,
                "sdk_max_retries": 0, "routing_rewrite_memory_persist_executed": False,
                "retrieval": "four frozen public NIST controls, BM25 top1; manually frozen topic queries",
                "semantic_labels": "assistant-authored development only", "protected_inputs": protected,
                "source_hashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in (CHUNKS, PLAN / "cases.json", ROOT / "agents/graph.py", ROOT / "agents/prompts.py",
                                            ROOT / "agents/sources.py", ROOT / "scripts/audit_flash_answers.py", Path(__file__))}}
    write(output / "manifest.json", manifest)
    if live:
        with (PLAN / "live-dispatch.lock").open("x", encoding="utf-8") as stream:
            json.dump({"output": str(output), "cases_sha256": file_hash(PLAN / "cases.json"),
                       "meaning": "This approval permits one run; preserve lock after failures."}, stream)
    calls = BoundedCalls(make_client(), output) if live else None
    index = BM25Index.build(chunks)

    class LocalRetriever:
        def retrieve(self, query, top_k, *unused):
            return index.search(query, top_k)

    rows, packets = [], []
    with patch.object(HybridRetriever, "get", return_value=LocalRetriever()), patch.object(config, "RETRIEVER_K", 1), patch.object(config, "ENABLE_EVIDENCE_SUBAGENT", False):
        for case in cases:
            row = {"id": case["id"], "kind": case["kind"], "status": "failed"}
            try:
                retrieved = await graph.retrieve({"search_query": case["search_query"]})
                contexts = retrieved["contexts"]
                if len(contexts) != 1 or contexts[0]["control_id"] != case["control_id"]:
                    raise ValueError("TargetControlNotRetrieved")
                if sum(len(c["text"]) for c in contexts) > 6000:
                    raise ValueError("ContextBudget")
                captured = []

                async def invoke(llm, messages, case=case, contexts=contexts, captured=captured):
                    payload = [{"role": "system" if m.type == "system" else "user", "content": m.content} for m in messages]
                    captured.extend(payload)
                    if calls:
                        text = await asyncio.to_thread(calls.invoke, payload, case["id"], "answer")
                    else:
                        text = case["fixture_answer_body"] + f"\n【出处】[资料1]《{contexts[0]['source']}》定位【{contexts[0]['locator']}】"
                    return AIMessage(content=text)

                with patch.object(graph, "get_answer_llm", return_value=object()), patch.object(graph, "llm_ainvoke_retry", invoke):
                    answer = (await graph.answer({"question": case["question"], "contexts": contexts}))["answer"]
                item = {"id": case["id"], "question": case["question"],
                        "contexts": [{k: c[k] for k in ("source", "locator", "source_url", "text")} for c in contexts], "answer": answer}
                packets.append(item)
                row.update(answer=answer, contexts=[source_preview(c) for c in contexts], messages=captured,
                           citation_structure=citation_checks(answer, contexts), semantic_support="not_scored")
                if calls:
                    grade = await asyncio.to_thread(calls.invoke, prompts(item, rubric), case["id"], "judge")
                    row["grade"] = validate_grade(json.loads(grade), item)
                    row["semantic_support"] = row["grade"]["scores"]["source_support"]
                row["status"] = "completed"
            except Exception as exc:
                row["error_type"] = type(exc).__name__
            rows.append(row)
            write(output / "results.json", rows)
            write(output / "answer-packet.json", packets)
            print(f"{case['id']}: {row['status']}; provider calls={calls.calls if calls else 0}", flush=True)
    gates = {"all_eight_executed": len(rows) == 8 and all(r["status"] == "completed" for r in rows),
             "protected_inputs_unchanged": all(file_hash(Path(p)) == h for p, h in protected.items())}
    if live:
        gates["all_semantic_dimensions_full"] = all(r.get("grade", {}).get("scores") == {"completeness": 2, "source_support": 2, "citation_support": 2} for r in rows)
        gates["citations_or_justified_refusal"] = all(not r.get("citation_structure", {}).get("errors", [1]) or
            (r["kind"] == "unsupported" and {e["code"] for e in r["citation_structure"]["errors"]} == {"no_parseable_citation"}
             and r.get("grade", {}).get("scores", {}).get("source_support") == 2) for r in rows)
    else:
        gates["all_citation_structures_valid"] = all(not r.get("citation_structure", {}).get("errors", [1]) for r in rows)
        gates["unsupported_fixture_semantics_remain_unknown"] = all(r.get("semantic_support") == "not_scored" for r in rows if r["kind"] == "unsupported")
    automatic_passed = all(gates.values())
    if live:
        # Provider scores alone cannot close semantic acceptance. Merge bound
        # source reviews with review_source_results.py after preserving this run.
        gates["source_review_completed"] = False
    summary = {"gates": gates, "passed": all(gates.values()), "automatic_passed": automatic_passed,
               "provider_calls": calls.calls if calls else 0,
               "conservative_cost_usd": None if calls and calls.unknown else round(calls.spent, 6) if calls else 0,
               "observed_cost_lower_bound_usd": round(calls.spent, 6) if calls else 0,
               "stop_reason": calls.stopped if calls else None, "real_model": live, "independent_human_review": False}
    write(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return summary["passed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--execute", action="store_true", help="Approved 8 answers + 8 reviews, max USD 0.10")
    args = parser.parse_args()
    if not asyncio.run(execute(args.output, args.execute)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
