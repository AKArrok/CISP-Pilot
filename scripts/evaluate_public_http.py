"""Approved public-NIST requests through ASGI HTTP, real models and isolated SQL."""
import argparse
import functools
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from unittest.mock import patch
from urllib.parse import urlsplit

from langchain_core.callbacks import BaseCallbackHandler

ROOT = Path(__file__).resolve().parents[1]
MIN_CASE_INTERVAL_SECONDS = 3.1  # Stay below the application's 20 new threads/minute.


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pace_case(previous_start):
    if previous_start is not None:
        delay = MIN_CASE_INTERVAL_SECONDS - (time.monotonic() - previous_start)
        if delay > 0:
            time.sleep(delay)
    return time.monotonic()


def answer_system(packet, manifest, default):
    override = manifest.get("fallback_system_override", False)
    if type(override) is not bool:
        raise ValueError("Frozen fallback override must be boolean")
    if not override:
        return default
    value = json.loads((packet / "fallback-system.json").read_text(encoding="utf-8"))["system"]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Frozen fallback system prompt must be nonempty text")
    return value


class Receipts(BaseCallbackHandler):
    """Fail closed on unknown usage; persist raw framework results, never headers."""
    raise_error = True

    def __init__(self, output, max_calls, budget):
        self.output, self.max_calls, self.budget = output, max_calls, budget
        self.lock = threading.RLock()
        self.calls, self.cost, self.unknown = 0, 0.0, False
        self.pending = {}
        self.stopped = False

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        with self.lock:
            if self.stopped or self.unknown or self.calls >= self.max_calls or self.cost + .008 > self.budget:
                raise RuntimeError("Batch dispatch stopped")
            self.calls += 1
            self.unknown = True
            self.pending[str(run_id)] = (time.monotonic(), (kwargs.get("metadata") or {}).get("langgraph_node"))
            write(self.output / "summary.json", self.snapshot())

    def on_llm_end(self, response, *, run_id, **kwargs):
        from agents.usage import receipt
        with self.lock:
            started, stage = self.pending.pop(str(run_id))
            message = response.generations[0][0].message
            usage = receipt(message)
            record = {"run_id": str(run_id), "stage": stage, "usage": usage,
                      "elapsed_seconds": time.monotonic() - started,
                      "receipt_scope": "raw LangChain LLMResult, not HTTP wire body",
                      "response": response.model_dump(mode="json", serialize_as_any=True)}
            with (self.output / "receipts.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            if any(value is None for value in usage.values()):
                self.stopped = True
                raise RuntimeError("Unknown provider usage")
            self.cost += (usage["prompt_tokens"] * .3 + usage["completion_tokens"] * 1.2) / 1e6
            self.unknown = bool(self.pending)
            write(self.output / "summary.json", self.snapshot())
            finish = message.response_metadata.get("finish_reason")
            if (finish not in {"stop", "tool_calls"} or usage["completion_tokens"] > 1024
                    or usage["prompt_tokens"] > 5500 or self.cost > self.budget
                    or message.additional_kwargs.get("reasoning_content")):
                self.stopped = True
                write(self.output / "summary.json", self.snapshot())
                raise RuntimeError("Provider receipt outside frozen bounds")

    def on_llm_error(self, error, *, run_id, **kwargs):
        with self.lock:
            self.stopped = True
            if str(run_id) in self.pending:
                self.unknown = True
            with (self.output / "errors.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"run_id": str(run_id), "error_type": type(error).__name__}) + "\n")
            write(self.output / "summary.json", self.snapshot())

    def snapshot(self):
        return {"dispatch_attempts": self.calls, "known_cost_estimate_usd": self.cost,
                "cost_unknown": self.unknown, "stopped": self.stopped}


def execute(packet, output, manifest):
    from dotenv import dotenv_values
    env = dotenv_values(ROOT / ".env", interpolate=False)
    base = os.getenv("LLM_BASE_URL") or env.get("LLM_BASE_URL") or "https://api.deepseek.com/v1"
    url = urlsplit(base)
    if (url.scheme != "https" or url.hostname != "api.deepseek.com" or url.port not in (None, 443)
            or url.username or url.password or url.query or url.fragment):
        raise ValueError("Only the approved official provider is allowed")
    data = output / "data"
    os.environ.update(PYTHON_DOTENV_DISABLED="1", CISP_DB_PATH=str(data / "cisp_qa.db"),
                      DATABASE_URL="", MYSQL_PASSWORD="", LLM_BASE_URL=base,
                      LLM_API_KEY=os.getenv("LLM_API_KEY") or env.get("LLM_API_KEY") or "",
                      LLM_MODEL="deepseek-flash", SIMPLE_LLM_MODEL="deepseek-flash",
                      EMBEDDING_BACKEND="dashscope", DASHSCOPE_API_KEY="", ARK_API_KEY="",
                      CISP_PUBLIC_ORIGIN="http://127.0.0.1:19629", REDIS_URL="redis://127.0.0.1:1/0",
                      CHAT_MAX_CALLS="6", CHAT_TIMEOUT_SECONDS="90", CHAT_MAX_OUTPUT_TOKENS="1024",
                      CHAT_MAX_ATTEMPTS_PER_CALL="1")
    import truststore
    truststore.inject_into_ssl()
    from scripts.prepare_demo import prepare
    prepare(data)
    import config
    for name, file in {"KB_CHUNKS_PATH": "kb_chunks.json", "QUESTIONS_PATH": "questions.json",
                       "VECTORS_PATH": "vectors.npz", "MEMORY_VECTORS_PATH": "memory-unused.npz",
                       "KNOWLEDGE_TAXONOMY_PATH": "knowledge_taxonomy.json",
                       "QUESTION_TAGS_PATH": "question_knowledge_tags.json",
                       "QUESTION_ATTRIBUTIONS_PATH": "question_attributions.json"}.items():
        setattr(config, name, str(data / file))
    config.DATA_DIR = str(data)
    config.COURSE_DIR = config.EXAM_DIR = str(data / "no-raw-files")
    for flag in ("ENABLE_DENSE_RETRIEVAL", "ENABLE_RERANKING", "ENABLE_QUERY_VARIANTS",
                 "ENABLE_FEATURE_RERANKING", "ENABLE_LEARNED_RANKING", "ENABLE_EVIDENCE_SUBAGENT"):
        setattr(config, flag, False)
    config.ENABLE_QUERY_VARIANTS = manifest.get("query_variants", False)
    config.RETRIEVER_K = 2
    chunks = json.loads((packet / "chunks.json").read_text(encoding="utf-8"))
    write(data / "kb_chunks.json", chunks)
    write(data / "vectors.npz.meta.json", chunks)
    import numpy as np
    np.savez_compressed(data / "vectors.npz", vectors=np.eye(len(chunks), dtype=np.float32))
    assets = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    assets.update(chunk_count=len(chunks), vector_count=len(chunks),
                  embedding_model="synthetic-protocol-fixture-not-semantic")
    write(data / "manifest.json", assets)
    from agents import graph, memory, prompts
    frozen = json.loads((packet / "prompts.json").read_text(encoding="utf-8"))
    for name, value in frozen.items():
        if getattr(prompts, name) != value:
            raise ValueError("Application prompt changed after freezing")
    import llms
    import server
    from storage import cache
    cache._client, cache._live = cache._MemClient(), False
    from fastapi.testclient import TestClient
    recorder = Receipts(output, manifest["max_calls"], manifest["budget_usd"])
    options = {"model": "deepseek-flash", "extra_body": {"thinking": {"type": "disabled"}},
               "max_tokens": 1024, "stream_usage": True, "callbacks": [recorder]}
    rows = []
    completed = False
    answer_node = graph.answer
    if manifest.get("answer_strategy") == "source_cards":
        from scripts.experimental_card_node import card_answer
        from scripts.outgoing_scope import load_outgoing_scope
        answer_node = functools.partial(card_answer, fallback=graph.answer,
            outgoing_scope=load_outgoing_scope(packet, manifest),
            protocol=manifest.get("answer_card_protocol", "units"),
            sources=json.loads((packet / "source-units.json").read_text(encoding="utf-8"))["sources"],
            translations=json.loads((packet / "translations.json").read_text(encoding="utf-8")),
            selector_prompt=json.loads((packet / "card-selector.json").read_text(encoding="utf-8"))["evidence_select_system"])
    try:
        with patch.object(prompts, "ANSWER_SYSTEM", answer_system(packet, manifest, prompts.ANSWER_SYSTEM)), \
             patch.object(graph, "answer", answer_node), \
             patch.object(graph, "get_simple_llm", functools.partial(llms.get_simple_llm, **options)), \
             patch.object(graph, "get_answer_llm", functools.partial(
                 llms.get_answer_llm, **options, temperature=manifest.get("answer_temperature", .3))), \
             patch.object(memory, "search_long_memory", return_value=[]), \
             patch.object(memory, "_save_vector_round", return_value=None), \
             TestClient(server.app, base_url=config.CISP_PUBLIC_ORIGIN) as client:
            client.headers["Origin"] = config.CISP_PUBLIC_ORIGIN
            credentials = {"username": "public-http-fixture", "password": "SyntheticOnly!Passphrase"}
            assert client.post("/api/auth/register", json=credentials).status_code == 201
            login = client.post("/api/auth/login", json=credentials)
            login.raise_for_status()
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            assert client.get("/api/ready").status_code == 200
            threads = {}
            previous_start = None
            for case in json.loads((packet / "cases.json").read_text(encoding="utf-8")):
                if recorder.stopped or recorder.unknown:
                    raise RuntimeError("Batch stopped before next request")
                previous_start = pace_case(previous_start)
                group = case["thread_group"]
                if group not in threads:
                    response = client.post("/api/threads")
                    response.raise_for_status()
                    threads[group] = response.json()["id"]
                started = time.monotonic()
                response = client.post("/chat/stream", json={"query": case["question"], "thread_id": threads[group]})
                response.raise_for_status()
                events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                trace = client.get("/api/traces/" + response.headers["x-request-id"])
                trace.raise_for_status()
                history = client.get("/api/history", params={"thread_id": threads[group]}).json()
                row = {"id": case["id"], "question": case["question"], "events": events,
                       "trace": trace.json(), "history_rows": len(history),
                       "client_case_started_monotonic": previous_start,
                       "elapsed_seconds": time.monotonic() - started}
                if manifest.get("answer_strategy") == "source_cards" or manifest.get("citation_layout"):
                    saved_answer = next((item.get("content") for item in reversed(history)
                                         if item.get("role") == "assistant"), None)
                    row["persisted_answer_sha256"] = (hashlib.sha256(saved_answer.encode()).hexdigest()
                                                       if isinstance(saved_answer, str) else None)
                rows.append(row)
                write(output / "results.json", rows)
                if not events or events[-1].get("type") != "done" or any(e.get("type") == "error" for e in events):
                    raise RuntimeError("HTTP request did not complete")
                print(case["id"] + ": completed", flush=True)
            completed = True
    finally:
        finished = sum(bool(row["events"]) and row["events"][-1].get("type") == "done"
                       and not any(e.get("type") == "error" for e in row["events"]) for row in rows)
        write(output / "summary.json", {**recorder.snapshot(), "completed_http_requests": finished,
                                       "recorded_http_requests": len(rows),
                                       "status": "completed" if completed else "stopped",
                                       "client_min_interval_seconds": MIN_CASE_INTERVAL_SECONDS,
                                       "transport": "in-process ASGI TestClient; real provider",
                                       "dense": False, "vector_memory": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((args.packet / "manifest.json").read_text(encoding="utf-8"))
    if type(manifest.get("query_variants", False)) is not bool:
        raise ValueError("Query variants must be an explicitly frozen boolean")
    if manifest.get("citation_layout") not in (None, "line-v1"):
        raise ValueError("Unknown frozen citation layout")
    strategy = manifest.get("answer_strategy", "default")
    if type(strategy) is not str or strategy not in {"default", "source_cards"}:
        raise ValueError("Unknown frozen answer strategy")
    protocol = manifest.get("answer_card_protocol", "units")
    if type(protocol) is not str or protocol not in {"units", "question_parts", "question_groups"}:
        raise ValueError("Unknown frozen card protocol")
    if protocol != "units" and strategy != "source_cards":
        raise ValueError("Question protocol requires source_cards strategy")
    from scripts.outgoing_scope import load_outgoing_scope
    load_outgoing_scope(args.packet, manifest)
    temperature = manifest.get("answer_temperature", .3)
    if type(temperature) not in (int, float) or not 0 <= temperature <= 2:
        raise ValueError("Answer temperature must be a number from 0 to 2")
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != manifest["executor_sha256"]:
        raise ValueError("Frozen executor changed")
    for name, digest in manifest["frozen_sha256"].items():
        if hashlib.sha256((args.packet / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Frozen input changed")
    if manifest.get("fallback_system_override"):
        if "fallback-system.json" not in manifest["frozen_sha256"]:
            raise ValueError("Fallback override must be frozen")
    answer_system(args.packet, manifest, "unchanged default")
    if not args.execute:
        print(json.dumps(manifest))
        return
    authorization = json.loads((args.packet / "authorization.json").read_text(encoding="utf-8"))
    if authorization != {"max_calls": manifest["max_calls"], "budget_usd": manifest["budget_usd"],
                         "manifest_sha256": hashlib.sha256((args.packet / "manifest.json").read_bytes()).hexdigest()}:
        raise ValueError("Authorization mismatch")
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "executor.py").write_bytes(Path(__file__).read_bytes())
    with (args.packet / "dispatch.lock").open("x", encoding="utf-8") as stream:
        stream.write("One approved batch; never remove to retry.\n")
    default = ROOT / "data/cisp_qa.db"
    before = hashlib.sha256(default.read_bytes()).hexdigest()
    try:
        execute(args.packet, args.output.resolve(), manifest)
    except Exception as error:
        write(args.output / "failure.json", {"error_type": type(error).__name__,
              "http_status": getattr(getattr(error, "response", None), "status_code", None)})
        raise
    finally:
        after = hashlib.sha256(default.read_bytes()).hexdigest()
        write(args.output / "default-db.json", {"before": before, "after": after, "unchanged": before == after})


if __name__ == "__main__":
    main()
