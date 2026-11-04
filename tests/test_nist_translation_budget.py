"""Synthetic transport only: no provider, private course file or real DB access."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scripts import translate_nist_search as translator


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def batch(tmp_path, monkeypatch):
    monkeypatch.setattr(translator, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data/cisp_qa.db").write_bytes(b"synthetic read-only sentinel")
    packet = tmp_path / "packet"
    packet.mkdir()
    write(packet / "chunks.json", [{"id": "synthetic", "text": "Original public prose"}])
    write(packet / "segments.json", [{"id": f"part-{i}", "control_id": "synthetic", "source_text": "Public prose"}
                                     for i in range(2)])
    (packet / "system-prompt.txt").write_text("Translate source only", encoding="utf-8")

    def freeze():
        manifest = {"max_calls": 2, "budget_usd": .02,
                    "executor_sha256": hashlib.sha256(Path(translator.__file__).read_bytes()).hexdigest(),
                    "frozen_sha256": {name: hashlib.sha256((packet / name).read_bytes()).hexdigest()
                                      for name in ("chunks.json", "segments.json", "system-prompt.txt")}}
        write(packet / "manifest.json", manifest)
        write(packet / "authorization.json", {"max_calls": 2, "budget_usd": .02,
                                               "manifest_sha256": hashlib.sha256((packet / "manifest.json").read_bytes()).hexdigest()})

    freeze()
    monkeypatch.setitem(sys.modules, "dotenv", SimpleNamespace(dotenv_values=lambda *a, **kw: {}))
    monkeypatch.setitem(sys.modules, "truststore", SimpleNamespace(inject_into_ssl=lambda: None))
    monkeypatch.setitem(sys.modules, "tiktoken", SimpleNamespace(get_encoding=lambda name: SimpleNamespace(encode=list)))
    monkeypatch.setenv("LLM_API_KEY", "synthetic-only")
    return packet, tmp_path / "output", freeze


def fake_provider(monkeypatch, *, missing_usage=False, finish="stop"):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(usage=None if missing_usage else SimpleNamespace(prompt_tokens=10, completion_tokens=5),
                               choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content="合成译文"))],
                               model_dump=lambda: {"synthetic": True})

    def client(**kwargs):
        assert kwargs["base_url"] == "https://api.deepseek.com/v1"
        assert kwargs["max_retries"] == 0
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=client))
    return calls


@pytest.mark.parametrize("fault", ["changed_source", "empty", "duplicate"])
def test_invalid_frozen_batch_stops_before_transport(batch, fault):
    packet, output, freeze = batch
    if fault == "changed_source":
        (packet / "system-prompt.txt").write_text("tampered", encoding="utf-8")
    else:
        segments = json.loads((packet / "segments.json").read_text())
        write(packet / "segments.json", [] if fault == "empty" else [segments[0], segments[0]])
        freeze()
    with pytest.raises(ValueError):
        translator.execute(packet, output)
    assert not output.exists()
    assert not (packet / "dispatch.lock").exists()


@pytest.mark.parametrize("missing_usage, finish, unknown", [(True, "stop", True), (False, "length", False)])
def test_uncertain_or_truncated_response_never_dispatches_next(batch, monkeypatch, missing_usage, finish, unknown):
    packet, output, _ = batch
    calls = fake_provider(monkeypatch, missing_usage=missing_usage, finish=finish)
    with pytest.raises(ValueError):
        translator.execute(packet, output)
    summary = json.loads((output / "summary.json").read_text())
    assert len(calls) == summary["dispatch_attempts"] == 1
    assert summary["status"] == "stopped"
    assert summary["cost_unknown"] is unknown
    assert summary["completed_segments"] == 0
    assert json.loads((output / "default-db.json").read_text())["unchanged"]


def test_success_preserves_source_and_forces_flash_without_thinking(batch, monkeypatch):
    packet, output, _ = batch
    source = (packet / "chunks.json").read_bytes()
    calls = fake_provider(monkeypatch)
    translator.execute(packet, output)
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "completed" and summary["completed_segments"] == 2
    assert summary["known_cost_estimate_usd"] == pytest.approx(.000018)
    assert (packet / "chunks.json").read_bytes() == source
    for call in calls:
        assert call["model"] == "deepseek-flash" and call["max_tokens"] == 1024
        assert call["extra_body"] == {"thinking": {"type": "disabled"}}
