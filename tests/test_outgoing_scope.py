"""Restricted private evaluation must fail before selector or fallback dispatch."""
import asyncio
import copy
import hashlib
import json
import sys

import pytest

from scripts import evaluate_public_http as runner
from scripts.outgoing_scope import context_digest, check_outgoing_scope, load_outgoing_scope
from test_experimental_card_node import setup


def scope_for(state):
    return {"questions": {hashlib.sha256(state["question"].encode()).hexdigest():
        [context_digest(context) for context in state["contexts"]]}}


def test_approved_context_runs_without_changing_original_scope(monkeypatch):
    node, state, calls, _ = setup(monkeypatch)
    scope = scope_for(state)
    original = copy.deepcopy(scope)
    assert asyncio.run(node(state, outgoing_scope=scope))["answer"]
    assert len(calls) == 1 and scope == original


@pytest.mark.parametrize("change", ["text", "page", "source", "locator", "source_url", "question", "extra", "empty", "history", "rewrite"])
def test_out_of_scope_cannot_reach_model_or_unknown_source_fallback(monkeypatch, change):
    node, state, calls, _ = setup(monkeypatch)
    scope = scope_for(state)
    if change in {"text", "source", "locator", "source_url"}:
        state["contexts"][0][change] = "Synthetic changed value"
    elif change == "page":
        state["contexts"][0]["page"] = 999
    elif change == "question":
        state["question"] += "changed"
    elif change == "extra":
        state["contexts"].append({"source": "Unapproved source", "text": "Synthetic extra"})
    elif change == "empty":
        state["contexts"] = []
    elif change == "history":
        state["messages"] = state["messages"] * 2
    else:
        state["search_query"] = "Unapproved rewritten query"
    with pytest.raises(ValueError, match="scope"):
        asyncio.run(node(state, outgoing_scope=scope))
    assert not calls


def test_context_scope_preserves_order_and_ignores_only_retrieval_scores():
    first = {"source": "Synthetic", "page": 1, "text": "First"}
    second = {"source": "Synthetic", "page": 2, "text": "Second"}
    scope = scope_for({"question": "Question", "contexts": [first, second]})
    check_outgoing_scope(scope, "Question", [{**first, "relevance_score": .8}, second])
    with pytest.raises(ValueError, match="scope"):
        check_outgoing_scope(scope, "Question", [second, first])


@pytest.mark.parametrize("setting", [True, "true", None, 1])
def test_scope_preflight_rejects_unfrozen_or_invalid_before_output(tmp_path, monkeypatch, setting):
    packet, output = tmp_path / "packet", tmp_path / "out"
    packet.mkdir()
    manifest = {"answer_strategy": "source_cards", "enforce_outgoing_scope": setting}
    (packet / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(sys, "argv", ["runner", "--packet", str(packet), "--output", str(output), "--execute"])
    with pytest.raises(ValueError, match="scope"):
        runner.main()
    assert not output.exists() and not (packet / "dispatch.lock").exists()


def test_scope_preflight_requires_exact_frozen_case_set(tmp_path):
    scope = scope_for({"question": "Approved", "contexts": [{"source": "Synthetic", "text": "Original"}]})
    (tmp_path / "outgoing-scope.json").write_text(json.dumps(scope))
    (tmp_path / "cases.json").write_text(json.dumps([{"question": "Other"}]))
    manifest = {"enforce_outgoing_scope": True, "answer_strategy": "source_cards",
                "frozen_sha256": {"outgoing-scope.json": hashlib.sha256((tmp_path / "outgoing-scope.json").read_bytes()).hexdigest()}}
    with pytest.raises(ValueError, match="scope"):
        load_outgoing_scope(tmp_path, manifest)


def test_valid_frozen_scope_reloads_and_tampering_fails(tmp_path):
    scope = scope_for({"question": "Approved", "contexts": [{"source": "Synthetic", "text": "Original"}]})
    path = tmp_path / "outgoing-scope.json"
    path.write_text(json.dumps(scope))
    (tmp_path / "cases.json").write_text(json.dumps([{"question": "Approved"}]))
    manifest = {"enforce_outgoing_scope": True, "answer_strategy": "source_cards",
                "frozen_sha256": {"outgoing-scope.json": hashlib.sha256(path.read_bytes()).hexdigest()}}
    assert load_outgoing_scope(tmp_path, manifest) == scope
    path.write_text(json.dumps({"questions": {}}))
    with pytest.raises(ValueError, match="scope"):
        load_outgoing_scope(tmp_path, manifest)


def test_normal_batches_do_not_read_private_scope(tmp_path):
    assert load_outgoing_scope(tmp_path, {}) is None
