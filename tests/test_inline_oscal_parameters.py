import json

import pytest

from scripts.inline_oscal_parameters import expand_parameters, parameter_bindings


MARKER = "\nOriginal OSCAL parameter definitions (no values assigned by this extraction):\n"


def test_inline_definition_stays_with_each_original_parameter_use():
    parameter = {"id": "x.1", "label": "components", "guidelines": [{"prose": "user data components"}]}
    text = "[x.a] Backup user data in {{ insert: param, x.1 }}.\n[x.b] Other action." + MARKER + json.dumps([parameter])
    rendered, additions = expand_parameters(text)
    assert len(additions) == 1
    assert additions[0]["parameter_id"] == "x.1"
    assert rendered.index('user data components') < rendered.index('[x.b]')
    for item in reversed(additions):
        assert rendered[item["start"]:item["end"]] == item["inserted_text"]
        rendered = rendered[:item["start"]] + rendered[item["end"]:]
    assert rendered == text


def test_unstructured_text_is_unchanged():
    assert expand_parameters("Ordinary source text") == ("Ordinary source text", [])


@pytest.mark.parametrize("definitions", [[], [{"id": "other"}], [{"id": "x"}, {"id": "x"}]])
def test_missing_or_duplicate_definitions_cannot_silently_invent_bindings(definitions):
    with pytest.raises(ValueError):
        expand_parameters("Use {{ insert: param, x }}" + MARKER + json.dumps(definitions))


def test_parameter_ownership_contains_only_literal_referencing_clauses():
    parameters = [{"id": "scope", "label": "components"}, {"id": "frequency", "label": "frequency"}]
    text = ("[x.a] Back up user data in {{ insert: param, scope }}.\n"
            "[x.b] Back up system data {{ insert: param, frequency }}." + MARKER + json.dumps(parameters))
    bindings = parameter_bindings(text)
    assert bindings[0]["referenced_in"] == [{"line": 1,
        "original_clause": "[x.a] Back up user data in {{ insert: param, scope }}."}]
    assert bindings[1]["referenced_in"][0]["line"] == 2
    assert "system data" not in bindings[0]["referenced_in"][0]["original_clause"]


def test_unreferenced_parameter_definition_is_not_assigned_an_invented_clause():
    text = "No parameter use." + MARKER + json.dumps([{"id": "unused", "label": "events"}])
    assert parameter_bindings(text) == [{"parameter_id": "unused", "referenced_in": []}]
