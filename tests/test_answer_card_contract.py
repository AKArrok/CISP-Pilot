import hashlib
import json

import pytest

from scripts.answer_unit_contract import build_catalog, materialize_card_selection, render_card_answer
from test_answer_unit_contract import fixture


def translation_map(catalog, contexts):
    units = {}
    for unit in catalog.values():
        digest = hashlib.sha256(contexts[unit["source_id"]-1]["text"].encode()).hexdigest()
        units[f"{digest}:{unit['line']}:{unit['part']}"] = {
            "english_sha256": hashlib.sha256(unit["text"].encode()).hexdigest(),
            "reading_translation": "按用户需要考虑多语言。" if unit["part"] == 2 else "按风险考虑二次告知。"}
    return {"units": units, "parameters": {}}


def test_typed_card_renders_only_bound_source_translation_and_original():
    contexts, sources = fixture()
    catalog = build_catalog(contexts, sources)
    selected = materialize_card_selection('{"unit_ids":["s1:l1:p2"],"answer_type":"affirm"}', catalog, contexts)
    text = render_card_answer(selected, contexts, translation_map(catalog, contexts))
    assert "肯定" in text and "辅助译文" in text and "Consider languages" in text
    assert "risk" not in text and "风险" not in text
    assert "定位【x】" in text


@pytest.mark.parametrize("raw", [
    '{"unit_ids":["s1:l1:p2"],"answer_type":"invented"}',
    '{"unit_ids":[],"answer_type":"affirm"}',
    '{"unit_ids":[],"answer_type":"not_specified"}',
    '{"unit_ids":[],"answer_type":"insufficient","answer":"free text"}',
])
def test_card_selector_cannot_supply_uncontrolled_prose_or_fake_decision(raw):
    contexts, sources = fixture()
    with pytest.raises(ValueError):
        materialize_card_selection(raw, build_catalog(contexts, sources), contexts)


def test_translation_must_bind_actual_original_not_just_share_a_label():
    contexts, sources = fixture()
    catalog = build_catalog(contexts, sources)
    selected = materialize_card_selection('{"unit_ids":["s1:l1:p2"],"answer_type":"affirm"}', catalog, contexts)
    translations = translation_map(catalog, contexts)
    translations["units"][next(reversed(translations["units"]))]["english_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="translation"):
        render_card_answer(selected, contexts, translations)


def test_no_evidence_refusal_never_fabricates_a_source():
    contexts, sources = fixture()
    selected = materialize_card_selection('{"unit_ids":[],"answer_type":"insufficient"}', build_catalog(contexts, sources), contexts)
    assert "不足" in render_card_answer(selected, contexts, {"units": {}, "parameters": {}})
    assert "【出处】" not in render_card_answer(selected, contexts, {"units": {}, "parameters": {}})


def test_insufficient_card_names_original_question_without_global_absence_claim():
    contexts, sources = fixture()
    selected = materialize_card_selection('{"unit_ids":[],"answer_type":"insufficient"}', build_catalog(contexts, sources), contexts)
    question = "每年的采购预算金额是多少？"
    rendered = render_card_answer(selected, contexts, {"units": {}, "parameters": {}}, question=question)
    assert question in rendered and "整个知识库" not in rendered and "【出处】" not in rendered


def test_native_parameter_annotation_is_bound_to_original_definition_and_selected_action():
    from scripts.compile_nist_source_units import compile_units
    from scripts.inline_oscal_parameters import MARKER
    definition = {"id": "scope", "label": "components", "guidelines": [{"prose": "components containing user data"}]}
    text = "[x.a] Back up user data in {{ insert: param, scope }}." + MARKER + json.dumps([definition])
    contexts = [{"source": "synthetic", "locator": "x", "source_url": "https://example.invalid", "text": text}]
    catalog = build_catalog(contexts, compile_units(contexts)["sources"])
    selected = materialize_card_selection('{"unit_ids":["s1:l1:p1"],"answer_type":"explain"}', catalog, contexts)
    translations = translation_map(catalog, contexts)
    translations["parameters"]["scope"] = {"original_definition": definition,
        "source_text_sha256": hashlib.sha256(text.encode()).hexdigest(), "reading_translation": "用户级信息备份所涉系统组件，未赋值。"}
    rendered = render_card_answer(selected, contexts, translations)
    assert "用户级信息备份所涉" in rendered and "s1:l1:p1" in rendered
    translations["parameters"]["scope"]["original_definition"] = {"id": "scope", "label": "frequency"}
    with pytest.raises(ValueError, match="parameter translation"):
        render_card_answer(selected, contexts, translations)


def test_native_child_clause_keeps_actual_parent_condition_without_other_actions():
    from scripts.compile_nist_source_units import compile_units
    text = ("Title\n[x_smt.a] For publicly accessible systems:\n"
            "[x_smt.a.1] Display authorized uses.\n[x_smt.b] An unrelated action.")
    contexts = [{"source": "synthetic", "locator": "x", "source_url": "https://example.invalid", "text": text}]
    catalog = build_catalog(contexts, compile_units(contexts)["sources"])
    selected = materialize_card_selection('{"unit_ids":["s1:l3:p1"],"answer_type":"explain"}', catalog, contexts)
    assert [unit["id"] for unit in selected["units"]] == ["s1:l2:p1", "s1:l3:p1"]
    assert selected["ancestor_unit_ids"] == ["s1:l2:p1"]
    assert "unrelated" not in json.dumps(selected)


def test_native_action_notice_distinguishes_unassigned_values_from_optional_actions():
    from scripts.compile_nist_source_units import compile_units
    text = "[x_smt] Conduct backups of privacy documentation."
    contexts = [{"source": "synthetic", "locator": "x", "source_url": "https://example.invalid", "text": text}]
    catalog = build_catalog(contexts, compile_units(contexts)["sources"])
    selected = materialize_card_selection('{"unit_ids":["s1:l1:p1"],"answer_type":"not_specified"}', catalog, contexts)
    translations = translation_map(catalog, contexts)
    next(iter(translations["units"].values()))["reading_translation"] = "对隐私文档执行备份。"
    rendered = render_card_answer(selected, contexts, translations)
    assert "参数值待定不表示动作可选" in rendered
    assert "适用条件" in rendered
    assert "已有的明确要求仍须" in rendered


def test_advisory_guidance_is_not_rendered_as_a_mandatory_statement():
    contexts, sources = fixture()
    catalog = build_catalog(contexts, sources)
    selected = materialize_card_selection('{"unit_ids":["s1:l1:p2"],"answer_type":"affirm"}', catalog, contexts)
    text = render_card_answer(selected, contexts, translation_map(catalog, contexts))
    assert "参数值待定不表示动作可选" not in text
