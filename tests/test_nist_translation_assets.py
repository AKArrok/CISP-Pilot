"""Public search translations must never replace the original citation evidence."""
import hashlib
import json
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "evaluation/nist-search-translation-v1"
RUN = ROOT / "docs/engineering-delivery/nist-search-translation-20261104-live"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name, count", [("translated-chunks.json", 8), ("passage-chunks.json", 22),
                                        ("multilevel-chunks.json", 30), ("multiscale-chunks.json", 101)])
def test_translated_search_assets_preserve_parent_source_and_parameters(name, count):
    originals = {chunk["control_id"]: chunk for chunk in read(PACKET / "chunks.json")}
    path = RUN / name
    manifest = read(RUN / "replay-manifest.json")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["frozen_sha256"][path.relative_to(ROOT).as_posix()]
    chunks = read(path)
    assert len(chunks) == len({chunk["id"] for chunk in chunks}) == count
    assert {chunk["control_id"] for chunk in chunks} == set(originals)
    for chunk in chunks:
        parent = originals[chunk["control_id"]]
        for field in ("text", "parameters", "source", "source_url", "locator", "license"):
            assert chunk[field] == parent[field]
        assert chunk.get("parent_id", parent["id"]) == parent["id"]
        assert "AI" in chunk["search_metadata"]["author"]


def test_translation_requests_contain_only_original_source_fragments():
    originals = {chunk["control_id"]: chunk for chunk in read(PACKET / "chunks.json")}
    segments, translations = read(PACKET / "segments.json"), read(RUN / "translations.json")
    manifest = read(PACKET / "manifest.json")
    assert len(segments) == len({row["id"] for row in segments}) == manifest["max_calls"] == 22
    assert {row["id"] for row in segments} == {row["id"] for row in translations}
    for row in segments:
        assert set(row) == {"id", "control_id", "source_text"}
        normalized = re.sub(r"\s+", " ", row["source_text"]).strip()
        source = re.sub(r"\s+", " ", originals[row["control_id"]]["text"]).strip()
        assert normalized and normalized in source
