"""Explicit own-file publication scope, with private-text and credential exclusion."""
import hashlib
import json
from pathlib import Path
import re

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
files = [".gitattributes", "EVALUATION_LOOP.md", "agents/graph.py", "llms.py", "tests/test_route_json_contract.py"]
own = ["README.md", "continuation.json", "freeze-decision-review.py", "dispatch-decision-review.py",
    "audit-decision-review.py", "prepare-route-json.py", "dispatch-route-json.py", "prepare-third-delivery.py",
    "decision-review-preflight-failure.json", "route-json-red.log", "route-json-green.log", "route-json-green2.log",
    "route-integrated.log", "route-integrated.xml"]
files += [(HERE / name).relative_to(ROOT).as_posix() for name in own]
for name in ("nist-public-http-question-groups-regression40-v3-20261107", "nist-public-http-route-json-20261107",
             "nist-decision-calibration-20261107", "nist-decision-calibration-v2-20261107"):
    for path in (ROOT / "evaluation" / name).iterdir():
        if path.is_file() and path.name not in {"authorization.json", "dispatch.lock"}:
            files.append(path.relative_to(ROOT).as_posix())
for name in ("nist-public-http-question-groups-regression40-v3-20261107-live", "nist-public-http-route-json-20261107-live",
             "nist-decision-calibration-v2-20261107-live"):
    for path in (ROOT / "docs/engineering-delivery" / name).iterdir():
        if path.is_file() and (path.suffix in {".json", ".jsonl", ".py"} or path.name == ".gitignore"):
            files.append(path.relative_to(ROOT).as_posix())
private = json.loads((ROOT / "data/review_packets/nist-mixed-private-20261104/outgoing-preview.json").read_text(encoding="utf-8"))
private_texts = [context["text"] for case in private for context in case["contexts"] if len(context["text"]) > 30]
secrets = [value for key, value in dotenv_values(ROOT / ".env", interpolate=False).items()
           if re.search(r"API_KEY|TOKEN|PASSWORD|SECRET", key) and value and len(value) >= 6]
manifest = HERE / "publish-third-files.json"
files.append(manifest.relative_to(ROOT).as_posix())
for name in files:
    if name == manifest.relative_to(ROOT).as_posix():
        continue
    data = (ROOT / name).read_text(encoding="utf-8")
    assert not any(value in data for value in secrets + private_texts), f"Excluded sensitive content in {name}"
    assert not re.search(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}", data), name
digest = hashlib.sha256((ROOT / "data/cisp_qa.db").read_bytes()).hexdigest()
assert digest == "3d6d78663b9378d8791f1692afcdd702c9476866d5458c3492f307766e9d5b62"
manifest.write_text(json.dumps({"files": sorted(set(files)), "private_fulltext_credential_or_jwt_matches": 0,
    "excluded": "preexisting unrelated changes, private data/index/db, authfiles, dispatch locks, all raw console",
    "default_db_sha256": digest}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"explicit_files": len(set(files)), "sensitive_matches": 0, "default_db_unchanged": True}))
