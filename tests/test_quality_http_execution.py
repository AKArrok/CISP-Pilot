"""Quality clients respect application limits and preserve safe failure evidence."""
import hashlib
from itertools import pairwise
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scripts import evaluate_public_http as runner


def test_case_pacing_stays_below_twenty_starts_per_minute(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(runner.time, "sleep", lambda delay: clock.update(now=clock["now"] + delay))
    previous, starts = None, []
    for _ in range(32):
        previous = runner.pace_case(previous)
        starts.append(previous)
        clock["now"] += .5
    assert all(b - a >= 3.09 for a, b in pairwise(starts))
    assert max(sum(start <= candidate < start + 60 for candidate in starts) for start in starts) <= 20


def test_slow_inference_does_not_get_an_extra_pacing_sleep(monkeypatch):
    monkeypatch.setattr(runner.time, "monotonic", lambda: 104.0)

    def unexpected_sleep(delay):
        raise AssertionError("Inference already exceeded the minimum interval")

    monkeypatch.setattr(runner.time, "sleep", unexpected_sleep)
    assert runner.pace_case(100.0) == 104.0


@pytest.mark.parametrize("flag", ["false", 0, 1, None])
def test_query_variant_experiment_rejects_nonboolean_flags_before_dispatch(tmp_path, monkeypatch, flag):
    packet, output = tmp_path / "packet", tmp_path / "output"
    packet.mkdir()
    (packet / "manifest.json").write_text(json.dumps({"query_variants": flag}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["evaluate", "--packet", str(packet), "--output", str(output), "--execute"])
    with pytest.raises(ValueError, match="explicitly frozen boolean"):
        runner.main()
    assert not output.exists()
    assert not (packet / "dispatch.lock").exists()


@pytest.mark.parametrize("http_status", [None, 429])
def test_failure_records_status_without_private_error_text(tmp_path, monkeypatch, http_status):
    class Rejected(Exception):
        response = SimpleNamespace(status_code=http_status)

    monkeypatch.setattr(runner, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    database = tmp_path / "data/cisp_qa.db"
    database.write_bytes(b"synthetic DB sentinel")
    packet, output = tmp_path / "packet", tmp_path / "output"
    packet.mkdir()
    manifest = {"max_calls": 1, "budget_usd": .08, "frozen_sha256": {},
                "executor_sha256": hashlib.sha256(Path(runner.__file__).read_bytes()).hexdigest()}
    (packet / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (packet / "authorization.json").write_text(json.dumps({"max_calls": 1, "budget_usd": .08,
        "manifest_sha256": hashlib.sha256((packet / "manifest.json").read_bytes()).hexdigest()}), encoding="utf-8")

    def reject(*args):
        raise Rejected("private question or provider error body")

    monkeypatch.setattr(runner, "execute", reject)
    monkeypatch.setattr(sys, "argv", ["runner", "--packet", str(packet), "--output", str(output), "--execute"])
    with pytest.raises(Rejected):
        runner.main()
    failure = json.loads((output / "failure.json").read_text())
    assert failure == {"error_type": "Rejected", "http_status": http_status}
    assert "private" not in (output / "failure.json").read_text()
    assert json.loads((output / "default-db.json").read_text())["unchanged"]
    assert database.read_bytes() == b"synthetic DB sentinel"


@pytest.mark.parametrize("temperature", [0, .3, 1.2])
def test_frozen_answer_temperature_reaches_real_executor_factory(tmp_path, monkeypatch, temperature):
    import config
    import dotenv
    import fastapi.testclient
    import llms
    import truststore
    from agents import graph
    from scripts import prepare_demo
    from storage import cache

    for name, value in list(vars(config).items()):
        if name.isupper():
            monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(runner.os, "environ", dict(runner.os.environ))
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *a, **kw: {})
    monkeypatch.setattr(truststore, "inject_into_ssl", lambda: None)
    monkeypatch.setattr(cache, "_client", cache._client)
    monkeypatch.setattr(cache, "_live", cache._live)
    packet, output = tmp_path / "packet", tmp_path / "output"
    packet.mkdir()
    output.mkdir()
    (packet / "chunks.json").write_text('[{"text":"synthetic source"}]', encoding="utf-8")
    (packet / "prompts.json").write_text('{}', encoding="utf-8")

    def prepare(data):
        data.mkdir()
        (data / "manifest.json").write_text('{}', encoding="utf-8")

    captured = {}

    def answer_factory(temperature=.3, **kwargs):
        captured["answer"] = temperature

    def simple_factory(temperature=.2, **kwargs):
        captured["simple"] = temperature

    class ProbeFinished(Exception):
        pass

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            graph.get_answer_llm()
            graph.get_simple_llm()
            raise ProbeFinished

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(prepare_demo, "prepare", prepare)
    monkeypatch.setattr(llms, "get_answer_llm", answer_factory)
    monkeypatch.setattr(llms, "get_simple_llm", simple_factory)
    monkeypatch.setattr(fastapi.testclient, "TestClient", Client)
    with pytest.raises(ProbeFinished):
        runner.execute(packet, output, {"answer_temperature": temperature,
                                       "max_calls": 1, "budget_usd": .03})
    assert captured == {"answer": temperature, "simple": .2}


@pytest.mark.parametrize("temperature", [True, None, "0.3", -.1, 2.1, float("nan")])
def test_invalid_frozen_answer_temperature_stops_before_dispatch(tmp_path, monkeypatch, temperature):
    packet, output = tmp_path / "packet", tmp_path / "output"
    packet.mkdir()
    (packet / "manifest.json").write_text(json.dumps({"answer_temperature": temperature}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["runner", "--packet", str(packet), "--output", str(output), "--execute"])
    with pytest.raises(ValueError, match="temperature"):
        runner.main()
    assert not output.exists()
    assert not (packet / "dispatch.lock").exists()
