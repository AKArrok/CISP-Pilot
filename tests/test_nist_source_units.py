import json

import pytest

from scripts.compile_nist_source_units import compile_units


def source(text, **extra):
    return {"source": "synthetic", "source_url": "https://example.invalid/source", "locator": "x", "text": text, **extra}


def test_duplicate_retrieval_aliases_are_one_original_and_never_become_source_units():
    result = compile_units([source("Original source.", embedding_text="DO_NOT_INCLUDE_GOLD"),
                            source("Original source.", embedding_text="different alias")])
    assert result["input_documents"] == 2 and result["canonical_sources"] == 1
    assert "DO_NOT_INCLUDE_GOLD" not in json.dumps(result)


def test_unit_boundaries_keep_initialism_and_condition_separate_with_exact_unicode_offsets():
    text = "[x_gdn] Use a U.S. system. Based on risk, consider another notice. Consider languages based on users."
    units = compile_units([source(text)])["sources"][0]["units"]
    assert len(units) == 3
    assert "U.S. system." in units[0]["text"]
    assert "risk" in units[1]["text"] and "risk" not in units[2]["text"]
    assert "".join(unit["text"] for unit in units) == text
    for unit in units:
        assert text[unit["start_codepoint"]:unit["end_codepoint"]] == unit["text"]


def test_compiler_refuses_missing_native_parameter_definition_instead_of_inventing_scope():
    text = ('[x.a] Back up {{ insert: param, missing }}.\n'
            'Original OSCAL parameter definitions (no values assigned by this extraction):\n'
            '[{"id":"other","label":"frequency"}]')
    with pytest.raises(ValueError, match="no original definition"):
        compile_units([source(text)])
