"""Reconcile approved public historical judgments and run the real zero-model citation auditor."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.database.exists() or args.output.exists():
        raise ValueError("Use new evidence and database paths; never overwrite an earlier round")
    os.environ["CISP_EVAL_DB_PATH"] = str(args.database.resolve())
    from storage import evaluation_store as store, evaluation_workspace as workspace, evaluation_jobs as jobs

    workspace.migrate()
    identifiers, evidence = [], []
    for path, original in zip((args.baseline, args.candidate), (
        "nist-public-http-question-parts-regression40-v2-20261107-live",
        "nist-public-http-question-groups-regression40-v3-20261107-live",
    ), strict=True):
        bundle = workspace.parse_bundle(path.read_bytes())
        identifier = store.import_bundle(bundle, "public-history-validation")["id"]
        identifiers.append(identifier)
        run = ROOT / "docs/engineering-delivery" / original
        audit = json.loads((run / "audit.json").read_text(encoding="utf-8"))
        review = json.loads((run / "semantic-review.json").read_text(encoding="utf-8"))
        structural = {row["id"]: row for row in audit["checks"]}
        semantic = {row["id"]: row for row in review["cases"]}
        expected = {key: "passed" if structural[key]["passed"] and semantic[key]["source_supported"] else "failed" for key in structural}
        actual = {row["id"]: row["status"] for row in workspace.cases(identifier, limit=2000)["items"]}
        assert actual == expected
        for result in bundle.results:
            assert result.answer_sha256 == semantic[result.id]["answer_sha256"]
        created = jobs.create(identifier, jobs.JobRequest(request_id="frozen-history-audit-v2"), "public-history-validation")
        jobs.execute(jobs.claim())
        job = jobs.get(created["id"])
        assert job["status"] == "completed" and job["processed"] == len(bundle.cases) and job["errors"] == 0
        for result in job["results"]:
            recorded = structural[result["case_id"]]["checks"].get("citation_structure")
            if recorded is not None:
                assert result["report"]["checks"]["citation_structure"] == recorded
        with store.database() as connection:
            summary = workspace.summary(connection, identifier)
        evidence.append({"file": str(path).replace("\\", "/"), "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "run_id": identifier, "version_id": summary["version_id"], "counts": summary["counts"],
                         "original_case_statuses_match": True, "answer_review_hashes_match": True,
                         "traces": sum(bool(result.trace) for result in bundle.results),
                         "known_token_rows": sum(result.prompt_tokens is not None for result in bundle.results),
                         "known_latency_rows": sum(result.latency_ms is not None for result in bundle.results),
                         "original_artifact_sha256": bundle.artifacts,
                         "offline_job": {key: job[key] for key in ("id", "status", "processed", "failed_checks", "errors", "model_calls", "auditor_sha", "snapshot_sha")}})
    comparison = workspace.compare(*identifiers)
    assert comparison["counts"] == {"fixed": 1, "regressed": 3, "unchanged": 36, "incomparable": 0,
                                    "insufficient_evidence": 0, "added": 0, "removed": 0}
    report = {"status": "passed", "model_calls": 0, "semantic_regraded": False, "inputs": evidence,
              "comparison": comparison["counts"], "changed_cases": [{key: row[key] for key in ("id", "before", "after", "change")}
                  for row in comparison["items"] if row["change"] != "unchanged"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"status": "passed", "cases_per_run": 40, "offline_audits": [item["offline_job"] for item in evidence],
                      "comparison": comparison["counts"], "model_calls": 0}))


if __name__ == "__main__":
    main()
