"""Replay old ablation arms under their original analyzer snapshot and current code."""
import hashlib
import json
from pathlib import Path
import runpy
from unittest.mock import patch

from retrieval import bm25

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def replay():
    manifest = json.loads((HERE / "verified-manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["frozen_sha256"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Frozen code or ablation evidence changed")
    broad = json.loads((HERE / "broad-manifest.json").read_text(encoding="utf-8"))
    with patch.object(bm25, "_STOPWORDS", set(broad["original_stopwords"])):
        first = runpy.run_path(str(HERE / "replay.py"))["run"]()
    expanded = runpy.run_path(str(HERE / "broad-replay.py"))["run"]()
    expected = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
    expected_broad = json.loads((HERE / "broad-results.json").read_text(encoding="utf-8"))
    if first != expected or expanded != expected_broad:
        raise ValueError("Frozen offline results did not reproduce")
    return {"exact_ablation_reproduced": True, "exact_180case_regression_reproduced": True,
            "candidate_passed": first["combined"]["passed"] and not expanded["summary"]["lost_ids"],
            "provider_calls": 0, "default_database_written": False}


if __name__ == "__main__":
    result = replay()
    with (HERE / "verified-results.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result))
