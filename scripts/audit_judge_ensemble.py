"""Audit two independently run critics over identical frozen calibration cases."""
import argparse
import hashlib
import json
from pathlib import Path

from scripts.audit_judge_calibration import audit


def ensemble(packet_a, run_a, packet_b, run_b):
    if (packet_a / "cases.json").read_bytes() != (packet_b / "cases.json").read_bytes():
        raise ValueError("Ensemble case inputs differ")
    manifests = [json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
                 for packet in (packet_a, packet_b)]
    for packet, manifest in zip((packet_a, packet_b), manifests, strict=True):
        for name, digest in manifest["frozen_sha256"].items():
            if hashlib.sha256((packet / name).read_bytes()).hexdigest() != digest:
                raise ValueError("Frozen input changed")
    if manifests[0]["baseline_sha256"] != manifests[1]["baseline_sha256"]:
        raise ValueError("Ensemble baselines differ")
    cases = json.loads((packet_a / "cases.json").read_text(encoding="utf-8"))
    a = audit(packet_a, run_a)["checks"]
    b = audit(packet_b, run_b)["checks"]
    if len(a) != len(cases) or len(b) != len(cases):
        raise ValueError("Ensemble must have exactly one result per case and critic")
    rows = []
    for case, left, right in zip(cases, a, b, strict=True):
        if left["id"] != case["id"] or right["id"] != case["id"]:
            raise ValueError("Ensemble result order differs")
        valid = left["status"] == right["status"] == "validated"
        # Both audits retain the same prior scores except for source support.
        score = min(left["score"], right["score"]) if valid else None
        rows.append({"id": case["id"], "valid": valid, "score": score,
                     "passed": valid and case["minimum"] <= score <= case["maximum"]})
    return {"passed": all(row["passed"] for row in rows), "checks": rows,
            "independent_human_review": False,
            "method": "two validated critics, stricter source support; same primary for other dimensions"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("packet-a", "run-a", "packet-b", "run-b", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    result = ensemble(args.packet_a, args.run_a, args.packet_b, args.run_b)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"passed": result["passed"], "matches": sum(r["passed"] for r in result["checks"])}))
