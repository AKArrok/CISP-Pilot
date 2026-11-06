import json

import pytest

from scripts.inline_oscal_parameters import expand_parameters


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
