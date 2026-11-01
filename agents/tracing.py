"""Bounded request observations without prompts, responses or raw exceptions."""
from contextvars import ContextVar
import threading
import time
import re

from langchain_core.callbacks import BaseCallbackHandler
from agents.usage import receipt, check_output_budget, service_receipt

current_trace = ContextVar("request_trace", default=None)


class RequestTrace:
    def __init__(self, request_id):
        self.request_id = request_id
        self.started = time.perf_counter()
        self.lock = threading.Lock()
        self.events = []
        self.dropped = 0
        self.conversation_commit = "not_confirmed"
        self.model_calls = []
        self.dropped_model_calls = 0
        self.service_calls = []
        self.dropped_service_calls = 0

    def start_service_call(self, kind, client_backend, requested_model, stage=None):
        with self.lock:
            if len(self.service_calls) >= 32:
                self.dropped_service_calls += 1
                return None
            call = {"kind": kind, "client_backend": client_backend,
                    "requested_model": _model_name(requested_model), "response_model": None,
                    "stage": stage if stage in {"route", "rewrite", "retrieve", "recall_memory", "answer", "persist", "chitchat", "quiz_hint"} else None,
                    "status": "started", "usage": service_receipt(None), "response_observed": False,
                    "started_ms": round((time.perf_counter() - self.started) * 1000, 1)}
            self.service_calls.append(call)
            return call

    def finish_service_call(self, call, status, response=None):
        if call is None:
            return
        with self.lock:
            if response is not None:
                model = response.get("model") if isinstance(response, dict) else getattr(response, "model", None)
                call.update(response_model=_model_name(model), usage=service_receipt(response), response_observed=True)
            call.update(status=status, ended_ms=round((time.perf_counter() - self.started) * 1000, 1))

    def start_model_call(self, requested_model, stage=None):
        with self.lock:
            if len(self.model_calls) >= 32:
                self.dropped_model_calls += 1
                return None
            call = {"requested_model": _model_name(requested_model), "response_model": None,
                    "stage": stage if stage in {"route", "rewrite", "retrieve", "recall_memory", "answer", "persist", "chitchat", "quiz_hint"} else None,
                    "status": "started", "usage": receipt(None), "response_observed": False,
                    "started_ms": round((time.perf_counter() - self.started) * 1000, 1)}
            self.model_calls.append(call)
            return call

    def finish_model_call(self, call, status, message=None):
        if call is None:
            return
        with self.lock:
            if message is not None:
                metadata = getattr(message, "response_metadata", None) or {}
                call.update(response_model=_model_name(metadata.get("model_name") or metadata.get("model")),
                            usage=receipt(message), response_observed=True)
            call.update(status=status, ended_ms=round((time.perf_counter() - self.started) * 1000, 1))

    def observe(self, stage, outcome):
        with self.lock:
            if len(self.events) < 128:
                self.events.append({"stage": stage, "outcome": outcome,
                                    "elapsed_ms": round((time.perf_counter() - self.started) * 1000, 1)})
            else:
                self.dropped += 1
            if stage == "conversation_commit" and outcome == "confirmed":
                self.conversation_commit = "confirmed"

    def snapshot(self):
        with self.lock:
            return {"request_id": self.request_id, "events": list(self.events),
                    "dropped_events": self.dropped, "conversation_commit": self.conversation_commit,
                    "model_calls": [dict(call, usage=dict(call["usage"])) for call in self.model_calls],
                    "dropped_model_calls": self.dropped_model_calls,
                    "service_calls": [dict(call, usage=dict(call["usage"])) for call in self.service_calls],
                    "dropped_service_calls": self.dropped_service_calls,
                    "service_call_scope": "application_embedding_rerank_attempts_excludes_legacy_sdk_internal_retries",
                    "cost": {"amount": None, "currency": None, "rate_source": None,
                             "status": "unknown_unbound_pricing", "scope": "observed_receipts_not_provider_invoice"},
                    "model_call_scope": "application_chat_attempts_excludes_sdk_internal_retries_embedding_reranking",
                    "scope": "history_graph_nodes_sql_commit_optional_memory_retrieval_observations"}


def observe(stage, outcome):
    trace = current_trace.get()
    if trace is not None:
        trace.observe(stage, outcome)


def _model_name(value):
    if isinstance(value, str) and len(value) <= 128 and "://" not in value and re.fullmatch(r"[A-Za-z0-9_.:/-]+", value):
        return value
    return None


class ModelReceiptCallback(BaseCallbackHandler):
    """Keep raw chat receipts even when a downstream structured parser fails."""
    run_inline = True

    def __init__(self, trace, call):
        self.trace, self.call = trace, call

    def on_llm_end(self, response, **kwargs):
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                if message is not None:
                    check_output_budget(message)
                    self.trace.finish_model_call(self.call, "response_received", message)
                    return
