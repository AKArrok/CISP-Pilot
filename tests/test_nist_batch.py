import hashlib
from pathlib import Path

from scripts.eval_nist_batch import input_token_limit, load, record_failure, request_content, retrieve, select_retrieval_arm


PACKET = Path(__file__).resolve().parents[1] / "evaluation/nist-development-v1"


def test_input_estimate_default_and_hard_ceiling():
    import pytest
    assert input_token_limit({}) == 5000
    assert input_token_limit({"max_estimated_input_tokens": 5500}) == 5500
    for invalid in (True, 0, -1, 5501, 6000, "5500", 5500.0):
        with pytest.raises(ValueError, match="integer"):
            input_token_limit({"max_estimated_input_tokens": invalid})


def test_frozen_retrieval_arm_does_not_change_to_fit_test_labels():
    import pytest
    metrics = {"bilingual": {"top1": 1, "top4": 2}, "source_only": {"top1": 3, "top4": 3}}
    assert select_retrieval_arm(metrics, "bilingual") == "bilingual"
    assert select_retrieval_arm(metrics) == "source_only"
    with pytest.raises(ValueError, match="Unknown frozen"):
        select_retrieval_arm(metrics, "nonexistent")


def test_line_evidence_protocol_does_not_require_answer_before_generation():
    import json
    case = {"question": "question", "reference_answer": "hidden label"}
    row = {"question": "question", "contexts": [{"text": "source", "source": "NIST"}]}
    manifest = {"evidence_protocol": "line_ids"}
    assert request_content("answer", case, row, "public source", manifest) == "public source\n问题：question"
    row["answer"] = "generated answer"
    content = request_content("judge", case, row, "public source", manifest)
    assert json.loads(content)["answer_lines"] == [{"line": 1, "text": "generated answer"}]
    assert "hidden label" not in content


def test_claim_focus_keeps_original_lines_and_does_not_supply_verdicts():
    import json
    row = {"question": "question", "answer": "普通描述\n即使前提不成立仍须执行\n不能自行起草",
           "contexts": [{"source": "NIST", "text": "source"}]}
    payload = json.loads(request_content("judge", {"question": "question", "label": "hidden"}, row, "",
                                        {"evidence_protocol": "line_ids", "claim_focus": True}))
    assert payload["focus_claims"] == payload["answer_lines"][1:]
    assert all(set(line) == {"line", "text"} for line in payload["focus_claims"])
    isolated = json.loads(request_content("judge", {"question": "question"}, row, "",
                                         {"evidence_protocol": "line_ids", "claim_focus": True, "claim_only": True}))
    assert "question" not in isolated
    assert isolated["answer_lines"] == payload["focus_claims"]
    assert isolated["contexts"] == payload["contexts"]


def test_claim_only_without_focus_removes_question_without_dropping_answer_lines():
    import json
    row = {"question": "leading question", "answer": "first claim\nsecond claim",
           "contexts": [{"source": "synthetic", "text": "original evidence"}]}
    payload = json.loads(request_content("judge_candidate", {}, row, "", {
        "evidence_protocol": "line_ids", "claim_only": True, "claim_focus": False}))
    assert "question" not in payload
    assert payload["answer_lines"] == [{"line": 1, "text": "first claim"},
                                       {"line": 2, "text": "second claim"}]
    assert payload["contexts"][0]["lines"] == [{"line": 1, "text": "original evidence"}]


def test_claim_flags_reject_nonboolean_json_before_creating_output(tmp_path, monkeypatch):
    import json
    import sys
    import pytest
    from scripts import eval_nist_batch

    packet = tmp_path / "packet"
    packet.mkdir()
    for flag in ("claim_only", "claim_focus", "guidance_segments", "native_parameter_bindings"):
        for invalid in (0, 1, "false", None):
            (packet / "manifest.json").write_text(json.dumps({flag: invalid}), encoding="utf-8")
            output = tmp_path / f"{flag}-{invalid}"
            monkeypatch.setattr(sys, "argv", ["eval", "--packet", str(packet), "--output", str(output)])
            with pytest.raises(ValueError, match="boolean"):
                eval_nist_batch.main()
            assert not output.exists()
            assert not (packet / "dispatch.lock").exists()


def test_declared_executor_hash_is_checked_before_output_or_dispatch(tmp_path, monkeypatch):
    import json
    import sys
    import pytest
    from scripts import eval_nist_batch

    packet, output = tmp_path / "packet", tmp_path / "output"
    packet.mkdir()
    (packet / "manifest.json").write_text(json.dumps({
        "executor_sha256": "0" * 64, "frozen_sha256": {}}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["eval", "--packet", str(packet), "--output", str(output), "--execute"])
    with pytest.raises(ValueError, match="executor"):
        eval_nist_batch.main()
    assert not output.exists()
    assert not (packet / "dispatch.lock").exists()


def test_atomic_protocol_preserves_target_and_context_without_question_or_labels():
    import json
    row = {"question": "leading original question", "answer": "these two parameters",
           "contexts": [{"source": "synthetic", "text": "source evidence"}]}
    case = {"fragment_context": "First and second parameters. These two parameters.", "minimum": 0}
    payload = json.loads(request_content("judge_candidate", case, row, "", {"evidence_protocol": "atomic_lines"}))
    assert payload["target_fragment"] == row["answer"]
    assert payload["answer_context"] == case["fragment_context"]
    assert "question" not in payload and "minimum" not in payload
    assert payload["contexts"][0]["lines"] == [{"line": 1, "text": "source evidence"}]


def test_interrupted_dispatch_is_persisted_before_sdk_call(tmp_path, monkeypatch):
    import json
    import sys
    from types import SimpleNamespace
    import dotenv
    import openai
    import pytest
    import tiktoken
    import truststore
    from scripts import eval_nist_batch

    packet, output = tmp_path / "packet", tmp_path / "output"
    packet.mkdir()
    context = {"source": "Synthetic", "locator": "x", "source_url": "https://example.invalid", "text": "Source"}
    for name, value in {"cases.json": [{"id": "c", "question": "Q", "answer": "A", "fixed_contexts": [context]}],
                        "chunks.json": [context], "prompts.json": {"judge_candidate_system": "JSON only"}}.items():
        (packet / name).write_text(json.dumps(value), encoding="utf-8")
    manifest = {"mode": "judge_calibration", "frozen_sha256": {}, "judge_arms": ["judge_candidate"],
                "evidence_protocol": "line_ids", "paid_proposal": {"calls": 1, "budget_usd": .03}}
    (packet / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (packet / "authorization.json").write_text(json.dumps({"max_calls": 1, "budget_usd": .03,
        "manifest_sha256": hashlib.sha256((packet / "manifest.json").read_bytes()).hexdigest()}), encoding="utf-8")
    observed = {}

    class Interrupted(BaseException):
        pass

    def create(**kwargs):
        observed.update(json.loads((output / "summary.json").read_text()))
        raise Interrupted

    monkeypatch.setattr(eval_nist_batch, "ROOT", tmp_path)
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *a, **kw: {})
    monkeypatch.setattr(truststore, "inject_into_ssl", lambda: None)
    monkeypatch.setattr(tiktoken, "get_encoding", lambda name: SimpleNamespace(encode=lambda text: [1]))
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(sys, "argv", ["eval", "--packet", str(packet), "--output", str(output), "--execute"])
    with pytest.raises(Interrupted):
        eval_nist_batch.main()
    assert observed["provider_calls"] == 1 and observed["cost_unknown"]
    assert observed["pending_call"] == {"id": "c", "stage": "judge_candidate"}


def test_frozen_packet_has_32_unique_development_questions_and_exact_sources():
    manifest = load(PACKET / "manifest.json")
    for filename, digest in manifest["frozen_sha256"].items():
        assert hashlib.sha256((PACKET / filename).read_bytes()).hexdigest() == digest
    cases = load(PACKET / "cases.json")
    chunks = load(PACKET / "chunks.json")
    assert len(cases) == len({case["question"] for case in cases}) == 32
    for case in cases:
        assert case["reference_evidence"] == next(c["text"] for c in chunks if c["control_id"] == case["target_control"])


def test_retrieval_comparison_keeps_all_arms_and_cases():
    cases, chunks = load(PACKET / "cases.json"), load(PACKET / "chunks.json")
    rows = retrieve(cases, chunks)
    assert len(rows) == 96
    assert all(r["target_rank"] == 1 for r in rows if r["arm"] == "bilingual")
    assert sum(r["target_rank"] is None for r in rows if r["arm"] == "source_only") == 31


def test_unrelated_query_does_not_gain_supplement_hits():
    rows = retrieve([{"id": "negative", "question": "TCP拥塞窗口慢启动", "target_control": None}], load(PACKET / "chunks.json"))
    assert all(not row["locators"] for row in rows)


def test_unknown_charge_stops_with_failed_and_unstarted_cases_retained(tmp_path):
    summary = {"cost_usd_conservative": .01, "cost_unknown": True, "provider_calls": 2}
    work = [{"id": value} for value in ["completed", "failed", "unstarted"]]
    record_failure(tmp_path, summary, work, [work[0]], "failed", RuntimeError("private-provider-details"))
    assert load(tmp_path / "summary.json")["cost_usd_conservative"] is None
    assert summary["known_cost_lower_bound_usd"] == .01
    assert [row["status"] for row in load(tmp_path / "execution-status.json")] == [
        "completed", "failed", "not_started_after_failure"]
    assert "private-provider-details" not in (tmp_path / "failure.json").read_text()
