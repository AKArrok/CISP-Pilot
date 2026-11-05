import hashlib
import json

import pytest

from scripts.audit_atomic_judge import audit


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def freeze(packet):
    names = ["cases.json", "parent-cases.json", "excluded-spans.json", "prompts.json"]
    write(packet / "manifest.json", {"frozen_sha256": {
        n: hashlib.sha256((packet / n).read_bytes()).hexdigest() for n in names}})


@pytest.fixture
def fixture(tmp_path):
    packet, run = tmp_path / "packet", tmp_path / "run"
    packet.mkdir()
    run.mkdir()
    text = "First claim.\nSecond claim.\n【出处】[资料1]《Synthetic》定位【x】"
    contexts = [{"text": "First evidence.\nSecond evidence.", "source": "Synthetic"}]
    parent = {"id": "p", "question": "Question", "answer": text, "fixed_contexts": contexts,
              "minimum": 2, "maximum": 2}
    children = [{"id": str(i), "parent_id": "p", "question": "Question", "answer": quote,
                 "answer_span": [text.index(quote), text.index(quote) + len(quote)],
                 "parent_answer_sha256": hashlib.sha256(text.encode()).hexdigest(), "fixed_contexts": contexts}
                for i, quote in enumerate(["First claim.", "Second claim."])]
    write(packet / "parent-cases.json", [parent])
    write(packet / "cases.json", children)
    write(packet / "excluded-spans.json", [{"parent_id": "p", "start": text.index("【出处】"),
                                            "end": len(text), "kind": "citation"}])
    write(packet / "prompts.json", {})
    freeze(packet)
    rows = [{"id": str(i), "judge_candidate": json.dumps({"support": "supported", "reason": "synthetic",
                                                          "source_ref": 1, "source_line": i + 1})}
            for i in range(2)]
    write(run / "results.json", rows)
    write(run / "summary.json", {"completed": 2, "provider_calls": 2, "cost_unknown": False})
    return packet, run


def test_complete_decomposition_aggregates_without_claiming_automatic_acceptance(fixture):
    result = audit(*fixture)
    assert result["range_checks_passed"] and result["parent_range_matches"] == 1
    assert result["checks"][1]["source_quote"] == "Second evidence."
    assert not result["automatic_production_acceptance"]


@pytest.mark.parametrize("change", ["omit", "hide", "alter_quote", "alter_source", "empty", "unfrozen"])
def test_decomposition_cannot_hide_or_change_original_claims(fixture, change):
    packet, run = fixture
    children = json.loads((packet / "cases.json").read_text(encoding="utf-8"))
    if change in {"omit", "hide"}:
        removed = children.pop()
        if change == "hide":
            excluded = json.loads((packet / "excluded-spans.json").read_text(encoding="utf-8"))
            excluded.append({"parent_id": "p", "start": removed["answer_span"][0],
                             "end": removed["answer_span"][1], "kind": "heading"})
            write(packet / "excluded-spans.json", excluded)
    elif change == "alter_quote":
        children[0]["answer"] = "Changed answer"
    elif change == "alter_source":
        children[0]["fixed_contexts"][0]["text"] = "A different source"
    elif change == "empty":
        children = []
    write(packet / "cases.json", children)
    if change != "unfrozen":
        freeze(packet)
    else:
        write(packet / "manifest.json", {"frozen_sha256": {}})
    with pytest.raises(ValueError):
        audit(packet, run)


def test_missing_result_is_not_inferred_supported(fixture):
    packet, run = fixture
    write(run / "results.json", [])
    result = audit(packet, run)
    assert not result["execution_complete"] and not result["range_checks_passed"]
    assert result["parent_checks"][0]["score"] is None


def test_supported_claim_requires_a_real_source_line(fixture):
    packet, run = fixture
    rows = json.loads((run / "results.json").read_text(encoding="utf-8"))
    rows[0]["judge_candidate"] = json.dumps({"support": "supported", "reason": "unsupported evidence",
                                           "source_ref": None, "source_line": None})
    write(run / "results.json", rows)
    result = audit(packet, run)
    assert result["checks"][0]["status"] == "invalid"
    assert not result["range_checks_passed"]


def test_substantive_claim_cannot_be_excused_as_a_heading(fixture):
    packet, run = fixture
    rows = json.loads((run / "results.json").read_text(encoding="utf-8"))
    rows[0]["judge_candidate"] = json.dumps({"support": "not_claim", "reason": "pretend heading"})
    write(run / "results.json", rows)
    result = audit(packet, run)
    assert result["checks"][0]["status"] == "invalid"
    assert not result["range_checks_passed"]


def test_added_context_cannot_supply_an_invented_answer_or_gold(fixture):
    packet, run = fixture
    cases = json.loads((packet / "cases.json").read_text(encoding="utf-8"))
    cases[0].update(fragment_context="invented expected verdict", context_span=[0, 12])
    write(packet / "cases.json", cases)
    freeze(packet)
    with pytest.raises(ValueError, match="context differs"):
        audit(packet, run)


def test_only_nonclaim_children_cannot_pass_by_empty_minimum(fixture):
    packet, run = fixture
    parent = json.loads((packet / "parent-cases.json").read_text(encoding="utf-8"))[0]
    parent["answer"] = "依据与说明："
    child = {"id": "h", "parent_id": "p", "question": parent["question"], "answer": parent["answer"],
             "answer_span": [0, len(parent["answer"])], "fixed_contexts": parent["fixed_contexts"],
             "parent_answer_sha256": hashlib.sha256(parent["answer"].encode()).hexdigest()}
    write(packet / "parent-cases.json", [parent])
    write(packet / "cases.json", [child])
    write(packet / "excluded-spans.json", [])
    freeze(packet)
    write(run / "results.json", [{"id": "h", "judge_candidate": json.dumps({
        "support": "not_claim", "reason": "pure heading"})}])
    write(run / "summary.json", {"completed": 1, "provider_calls": 1, "cost_unknown": False})
    result = audit(packet, run)
    assert result["checks"][0]["status"] == "not_claim"
    assert result["parent_checks"][0]["score"] is None and not result["range_checks_passed"]
