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
