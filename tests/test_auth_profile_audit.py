"""Audit regression on public synthetic metrics; no real DB, credentials or network."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from scripts import audit_auth_limit_profile as auditor


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    source = root / "docs/engineering-delivery/auth-limit-20261109"
    run = tmp_path / "run"
    run.mkdir()
    plan = json.loads((source / "plan.json").read_text(encoding="utf-8"))
    for name in ["summary.json", "scrypt.jsonl", "limit-boundary.json", "security-constants.json",
                 "resources.json", "cache-probe.json", "http-ready.json", *plan["frozen_sha256"]]:
        shutil.copyfile(source / name, run / name)
    workspace = tmp_path / "workspace"
    for name in plan["runtime_sha256"]:
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, path)
        plan["runtime_sha256"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (workspace / "data").mkdir()
    database = b"synthetic audit-only database bytes"
    (workspace / "data/cisp_qa.db").write_bytes(database)
    digest = hashlib.sha256(database).hexdigest()
    plan["default_db_before"] = digest
    (run / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    summary.update(default_db_before=digest, default_db_after=digest)
    (run / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (run / "server.log").write_bytes(b"Synthetic server ready\n")
    monkeypatch.setattr(auditor, "ROOT", workspace)
    return run


def test_valid_evidence_passes_and_preserves_existing_report(evidence):
    original = evidence / "audit.json"
    original.write_text("historical report", encoding="utf-8")
    result = auditor.audit(evidence, evidence / "new-audit.json")
    assert result["passed"] and all(result["checks"].values())
    assert original.read_text(encoding="utf-8") == "historical report"


@pytest.mark.parametrize("marker", [b"ERROR synthetic failure", b"Traceback synthetic failure"])
def test_server_error_cannot_be_recorded_as_pass(evidence, marker):
    (evidence / "server.log").write_bytes(marker)
    result = auditor.audit(evidence)
    assert not result["passed"]
    assert not result["checks"]["server_no_error_markers"]
    assert all(value for key, value in result["checks"].items() if key != "server_no_error_markers")
