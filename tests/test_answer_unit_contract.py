import hashlib
import json

import pytest

from scripts.answer_unit_contract import build_catalog, materialize_selection, render_unit_answer


def fixture():
    text = "[x_gdn] Based on risk, consider a second notice. Consider languages based on users."
    first = "[x_gdn] Based on risk, consider a second notice."
    digest = hashlib.sha256(text.encode()).hexdigest()
    contexts = [{"source": "synthetic", "locator": "x", "source_url": "https://example.invalid/source", "text": text}]
    sources = [{"source": "synthetic", "canonical_text_sha256": digest, "source_url": contexts[0]["source_url"], "locator": "x", "units": [
        {"id": digest + ":1:1", "parent_line": 1, "part": 1, "start_codepoint": 0, "end_codepoint": len(first), "text": first},
        {"id": digest + ":1:2", "parent_line": 1, "part": 2, "start_codepoint": len(first), "end_codepoint": len(text), "text": text[len(first):]}]}]
    return contexts, sources


def test_selection_materializes_only_selected_original_without_neighbor_condition():
    contexts, sources = fixture()
    catalog = build_catalog(contexts, sources)
    selected = materialize_selection('{"unit_ids":["s1:l1:p2"],"insufficient":false}', catalog, contexts)
    assert selected["units"][0]["text"] == " Consider languages based on users."
    assert "risk" not in json.dumps(selected)
    rendered = render_unit_answer('{"answer":"应考虑用户需要。","unit_ids":["s1:l1:p2"]}', selected, contexts)
    assert "Consider languages" in rendered and "risk" not in rendered
    assert "定位【x】" in rendered


@pytest.mark.parametrize("metadata,value", [("source", "Another source"), ("page", 9)])
def test_source_binding_rejects_wrong_name_or_page_even_when_text_matches(metadata, value):
    contexts, sources = fixture()
    contexts[0][metadata] = value
    with pytest.raises(ValueError, match="binding"):
        build_catalog(contexts, sources)


@pytest.mark.parametrize("raw", [
    '{"unit_ids":["unknown"],"insufficient":false}',
    '{"unit_ids":["s1:l1:p1","s1:l1:p1"],"insufficient":false}',
    '{"unit_ids":[],"insufficient":false}',
    '{"unit_ids":[],"insufficient":"true"}',
    '{"unit_ids":[],"insufficient":true,"quote":"invented"}',
])
def test_selection_rejects_invalid_or_invented_evidence(raw):
    contexts, sources = fixture()
    with pytest.raises(ValueError):
        materialize_selection(raw, build_catalog(contexts, sources), contexts)


def test_answer_cannot_cite_unselected_unit_or_rewrite_quote():
    contexts, sources = fixture()
    selected = materialize_selection('{"unit_ids":["s1:l1:p2"],"insufficient":false}', build_catalog(contexts, sources), contexts)
    for raw in ['{"answer":"A","unit_ids":["s1:l1:p1"]}',
                '{"answer":"A","unit_ids":["s1:l1:p2"],"quote":"invented"}']:
        with pytest.raises(ValueError):
            render_unit_answer(raw, selected, contexts)


def test_missing_evidence_can_render_scoped_refusal_without_false_citation():
    contexts, sources = fixture()
    selected = materialize_selection('{"unit_ids":[],"insufficient":true}', build_catalog(contexts, sources), contexts)
    rendered = render_unit_answer('{"answer":"本次资料不足以确认该数值。","unit_ids":[]}', selected, contexts)
    assert "不足" in rendered and "定位【x】" not in rendered


def test_catalog_rejects_wrong_original_binding_and_forged_offsets():
    contexts, sources = fixture()
    sources[0]["units"][1]["text"] = "made up"
    with pytest.raises(ValueError):
        build_catalog(contexts, sources)
    contexts, sources = fixture()
    sources[0]["canonical_text_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        build_catalog(contexts, sources)


def test_pipeline_request_moves_only_selected_unit_to_generation():
    from scripts.eval_nist_batch import request_content
    contexts, sources = fixture()
    case = {"question": "Should languages be considered?", "unit_sources": sources}
    row = {"contexts": contexts, "evidence_select": '{"unit_ids":["s1:l1:p2"],"insufficient":false}'}
    manifest = {"mode": "answer_unit_pipeline"}
    selection = json.loads(request_content("evidence_select", case, row, "", manifest))
    answer = json.loads(request_content("answer_candidate", case, row, "", manifest))
    assert "risk" in json.dumps(selection) and "risk" not in json.dumps(answer)
    assert selection["question"] == answer["question"] == case["question"]


def test_selected_parameter_definition_keeps_user_action_owner_without_other_actions():
    from scripts.compile_nist_source_units import compile_units
    from scripts.inline_oscal_parameters import MARKER
    parameters = [{"id": "scope", "label": "components", "guidelines": [{"prose": "components containing user data"}]}]
    text = "[x.a] Back up user data in {{ insert: param, scope }}.\n[x.b] Back up all system data." + MARKER + json.dumps(parameters)
    contexts = [{"source": "synthetic", "locator": "x", "source_url": "https://example.invalid", "text": text}]
    sources = compile_units(contexts)["sources"]
    selected = materialize_selection('{"unit_ids":["s1:l1:p1"],"insufficient":false}', build_catalog(contexts, sources), contexts)
    assert selected["parameter_definitions"][0]["original_definition"] == parameters[0]
    assert selected["parameter_definitions"][0]["referenced_unit_ids"] == ["s1:l1:p1"]
    assert selected["units"][0]["text"] == text.splitlines()[0]
    assert "all system data" not in json.dumps(selected)


def test_original_blank_lines_remain_numbered_but_cannot_be_selected_as_evidence():
    from scripts.compile_nist_source_units import compile_units
    contexts = [{"source": "synthetic", "locator": "x", "source_url": "https://example.invalid", "text": "First.\n\nThird."}]
    catalog = build_catalog(contexts, compile_units(contexts)["sources"])
    assert set(catalog) == {"s1:l1:p1", "s1:l3:p1"}
    with pytest.raises(ValueError):
        materialize_selection('{"unit_ids":["s1:l2:p1"],"insufficient":false}', catalog, contexts)
