import hashlib
import json
from pathlib import Path
import shutil

import pytest

from agents.sources import source_preview
from scripts.answer_unit_contract import (build_catalog, materialize_card_selection, render_card_answer,
    materialize_question_card, render_question_card, materialize_grouped_question_card)
from scripts.audit_public_http import audit
from scripts.compile_nist_source_units import compile_units
from test_answer_unit_contract import fixture
from test_answer_card_contract import translation_map


ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture(params=["units", "question_parts", "question_groups"])
def card_run(tmp_path, request):
    packet, output = tmp_path / "packet", tmp_path / "output"
    packet.mkdir()
    output.mkdir()
    contexts, sources = fixture()
    contexts[0]["domain"] = "安全管理"
    catalog = build_catalog(contexts, sources)
    translations = translation_map(catalog, contexts)
    raw = '{"unit_ids":["s1:l1:p2"],"answer_type":"affirm"}'
    answer = render_card_answer(materialize_card_selection(raw, catalog, contexts), contexts, translations, question="Q")
    if request.param == "question_parts":
        raw = '{"parts":[{"part_id":"q1","decision":"affirm","unit_ids":["s1:l1:p2"]}]}'
        selected = materialize_question_card(raw, "Q", catalog, contexts)
        answer = render_question_card(selected, contexts, translations, question="Q")
    elif request.param == "question_groups":
        raw = '{"parts":[{"part_ids":["q1"],"decision":"affirm","unit_ids":["s1:l1:p2"]}]}'
        selected = materialize_grouped_question_card(raw, "Q", catalog, contexts)
        answer = render_question_card(selected, contexts, translations, question="Q")
    for name, data in {"cases.json": [{"id": "c", "question": "Q", "target_locator": "x", "thread_group": "c"}],
                       "chunks.json": contexts, "source-units.json": compile_units(contexts),
                       "translations.json": translations}.items():
        write(packet / name, data)
    dependencies = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in ("scripts/answer_output_contract.py", "scripts/inline_oscal_parameters.py", "agents/sources.py")}
    write(packet / "runtime-source-hashes.json", dependencies)
    shutil.copyfile(ROOT / "scripts/answer_unit_contract.py", packet / "contract.py")
    manifest = {"answer_strategy": "source_cards", "answer_card_protocol": request.param,
        "executor_sha256": hashlib.sha256(b"synthetic executor").hexdigest(),
        "max_calls": 1, "budget_usd": .08, "max_output_tokens": 1024,
        "frozen_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in packet.iterdir()}}
    write(packet / "manifest.json", manifest)
    (output / "executor.py").write_bytes(b"synthetic executor")
    usage = {"prompt_tokens": 10, "completion_tokens": 8}
    row = {"id": "c", "question": "Q", "events": [{"type": "intent", "intent": "knowledge"},
        {"type": "contexts", "contexts": [source_preview(contexts[0])]}, {"type": "token", "content": answer}, {"type": "done"}],
        "history_rows": 2, "persisted_answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
        "trace": {"status": "completed", "trace": {"events": [{"stage": "answer_source_cards", "outcome": "rendered"}],
            "conversation_commit": "confirmed", "model_calls": [{"stage": "answer", "usage": usage}]}}}
    write(output / "results.json", [row])
    receipt = {"run_id": "synthetic1", "stage": "answer", "usage": usage,
        "response": {"generations": [[{"message": {"content": raw}}]]}}
    (output / "receipts.jsonl").write_text(json.dumps(receipt)+"\n", encoding="utf-8")
    write(output / "summary.json", {"cost_unknown": False, "stopped": False, "dispatch_attempts": 1,
        "known_cost_estimate_usd": .0000126, "status": "completed", "completed_http_requests": 1, "recorded_http_requests": 1})
    write(output / "default-db.json", {"before": "0"*64, "after": "0"*64, "unchanged": True})
    return packet, output


def test_transformed_card_requires_pinned_renderer_stream_and_persisted_hash(card_run):
    assert audit(*card_run)["passed"]


@pytest.mark.parametrize("fault", ["stream", "history", "receipt", "node_missing"])
def test_card_gate_rejects_unbound_transformations(card_run, fault):
    packet, output = card_run
    rows = json.loads((output / "results.json").read_text(encoding="utf-8"))
    if fault == "stream":
        next(event for event in rows[0]["events"] if event["type"] == "token")["content"] += "fabricated claim"
    elif fault == "history":
        rows[0]["persisted_answer_sha256"] = "f"*64
    elif fault == "node_missing":
        rows[0]["trace"]["trace"]["events"] = []
    else:
        receipt = json.loads((output / "receipts.jsonl").read_text(encoding="utf-8"))
        receipt["response"]["generations"][0][0]["message"]["content"] = '{"unit_ids":["unknown"],"answer_type":"affirm"}'
        (output / "receipts.jsonl").write_text(json.dumps(receipt)+"\n", encoding="utf-8")
    write(output / "results.json", rows)
    assert not audit(packet, output)["passed"]
