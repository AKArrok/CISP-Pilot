import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts.audit_public_http import audit


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "evaluation/nist-public-http-v1"
RUN = ROOT / "docs/engineering-delivery/nist-public-http-20261104-live"


def test_saved_real_http_preflight_has_bounded_evidence():
    result = audit(PACKET, RUN)
    assert result["passed"]
    assert result["independent_human_gold"] is False
    assert result["semantic_source_review"] == "required separately"


@pytest.mark.parametrize("fault", ["done", "history", "source", "trace", "unknown_cost", "missing_usage", "duplicate_receipt"])
def test_http_gate_rejects_incomplete_or_unbound_results(tmp_path, fault):
    for name in ("results.json", "summary.json", "default-db.json", "receipts.jsonl", "executor.py"):
        shutil.copyfile(RUN / name, tmp_path / name)
    rows = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    if fault == "done":
        rows[0]["events"].pop()
    elif fault == "history":
        rows[1]["history_rows"] = 2
    elif fault == "source":
        next(e for e in rows[0]["events"] if e["type"] == "contexts")["contexts"] = []
    elif fault == "trace":
        rows[0]["trace"]["trace"]["conversation_commit"] = "not_confirmed"
    elif fault == "unknown_cost":
        summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
        summary["cost_unknown"] = True
        (tmp_path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    else:
        receipts = [json.loads(line) for line in (tmp_path / "receipts.jsonl").read_text(encoding="utf-8").splitlines()]
        if fault == "missing_usage":
            receipts[0]["usage"] = {}
        else:
            receipts[1]["run_id"] = receipts[0]["run_id"]
        (tmp_path / "receipts.jsonl").write_text("\n".join(json.dumps(r) for r in receipts), encoding="utf-8")
    (tmp_path / "results.json").write_text(json.dumps(rows), encoding="utf-8")
    assert not audit(PACKET, tmp_path)["passed"]
    if fault == "done":
        output = tmp_path / "audit.json"
        process = subprocess.run([sys.executable, "-m", "scripts.audit_public_http", "--packet", str(PACKET),
                                  "--run", str(tmp_path), "--output", str(output)],
                                 cwd=ROOT, capture_output=True, check=False)
        assert process.returncode == 1
        assert json.loads(output.read_text(encoding="utf-8"))["passed"] is False
