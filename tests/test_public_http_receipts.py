import json
from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
import pytest

from scripts.evaluate_public_http import Receipts


def result(*, usage=True, finish="stop"):
    return LLMResult(generations=[[ChatGeneration(message=AIMessage(
        content="synthetic answer", response_metadata={"finish_reason": finish},
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15} if usage else None))]])


def test_receipts_bound_dispatch_and_persist_unknown_before_send(tmp_path):
    recorder = Receipts(tmp_path, 1, .08)
    identity = uuid4()
    recorder.on_chat_model_start({}, [[]], run_id=identity)
    assert json.loads((tmp_path / "summary.json").read_text())["cost_unknown"] is True
    recorder.on_llm_end(result(), run_id=identity)
    assert recorder.snapshot()["cost_unknown"] is False
    assert recorder.snapshot()["known_cost_estimate_usd"] == pytest.approx(.000009)
    with pytest.raises(RuntimeError, match="stopped"):
        recorder.on_chat_model_start({}, [[]], run_id=uuid4())
    assert recorder.calls == 1
    assert len((tmp_path / "receipts.jsonl").read_text().splitlines()) == 1


def test_unknown_usage_stops_later_dispatch(tmp_path):
    recorder = Receipts(tmp_path, 8, .08)
    identity = uuid4()
    recorder.on_chat_model_start({}, [[]], run_id=identity)
    with pytest.raises(RuntimeError, match="Unknown provider usage"):
        recorder.on_llm_end(result(usage=False), run_id=identity)
    with pytest.raises(RuntimeError, match="stopped"):
        recorder.on_chat_model_start({}, [[]], run_id=uuid4())
    assert recorder.unknown is True


def test_known_truncation_keeps_known_charge_and_stops(tmp_path):
    recorder = Receipts(tmp_path, 8, .08)
    identity = uuid4()
    recorder.on_chat_model_start({}, [[]], run_id=identity)
    with pytest.raises(RuntimeError, match="outside frozen bounds"):
        recorder.on_llm_end(result(finish="length"), run_id=identity)
    recorder.on_llm_error(RuntimeError("private details"), run_id=identity)
    assert recorder.stopped and not recorder.unknown
    assert "private details" not in (tmp_path / "errors.jsonl").read_text()
