import hashlib
import json

import pytest

from scripts import audit_judge_ensemble as module


def test_ensemble_requires_both_valid_and_matching_inputs(tmp_path, monkeypatch):
    cases = [{"id": "x", "minimum": 0, "maximum": 1}]
    packets = [tmp_path / "a", tmp_path / "b"]
    for packet in packets:
        packet.mkdir()
        data = json.dumps(cases).encode()
        (packet / "cases.json").write_bytes(data)
        manifest = {"frozen_sha256": {"cases.json": hashlib.sha256(data).hexdigest()},
                    "baseline_sha256": "same"}
        (packet / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    def stub(packet, run):
        return {"checks": [{"id": "x", "status": "validated" if run == "ok" else "invalid",
                            "score": 1}]}
    monkeypatch.setattr(module, "audit", stub)
    assert module.ensemble(packets[0], "ok", packets[1], "ok")["passed"]
    assert not module.ensemble(packets[0], "ok", packets[1], "bad")["passed"]
    (packets[1] / "cases.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="inputs differ"):
        module.ensemble(packets[0], "ok", packets[1], "ok")
