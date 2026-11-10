"""Reproduce catalog lineage and unchanged historical judgments with zero model calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--knowledge", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--local-knowledge", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.database.exists() or args.output.exists():
        raise ValueError("Use fresh validation paths to preserve earlier evidence")
    os.environ["CISP_EVAL_DB_PATH"] = str(args.database.resolve())
    from storage import data_catalog as data, evaluation_store as store, evaluation_workspace as evaluation

    evaluation.migrate()
    data.migrate()
    knowledge = data.Snapshot.model_validate_json(args.knowledge.read_bytes())
    sid = data.import_snapshot(knowledge, "local-verification-agent")["id"]
    inputs = []
    run_ids = []
    for path in (args.baseline, args.candidate):
        bundle = evaluation.parse_bundle(path.read_bytes())
        rid = store.import_bundle(bundle, "local-verification-agent")["id"]
        run_ids.append(rid)
        counts, bindings, candidate_contexts, ambiguous = {}, {}, 0, 0
        for case in bundle.cases:
            detail = evaluation.detail(rid, case.id)
            counts[detail["status"]] = counts.get(detail["status"], 0) + 1
            link = data.lineage(sid, rid, case.id)
            bindings[link["version_binding"]] = bindings.get(link["version_binding"], 0) + 1
            candidate_contexts += sum(bool(row["candidates"]) for row in link["matches"])
            ambiguous += sum(row["ambiguous"] for row in link["matches"])
        assert store.digest(store.get_run(rid)["payload"]) == rid
        inputs.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "run_id": rid,
                       "counts": counts, "version_bindings": bindings, "contexts_with_candidates": candidate_contexts,
                       "ambiguous_contexts": ambiguous, "original_import_unchanged": True})
    result = {"status": "passed", "model_calls": 0, "snapshot_id": sid,
              "knowledge_file_sha256": hashlib.sha256(args.knowledge.read_bytes()).hexdigest(),
              "quality": data.snapshot(sid)["quality"], "runs": inputs,
              "comparison": evaluation.compare(*run_ids)["counts"], "semantic_regraded": False}
    if args.local_knowledge:
        local = data.Snapshot.model_validate_json(args.local_knowledge.read_bytes())
        local_id = data.import_snapshot(local, "local-verification-agent")["id"]
        # Metadata/text stays in the local snapshot and isolated DB; public report contains counts/hashes only.
        checks = data.snapshot(local_id)["quality"]
        result["local_private_observation"] = {"snapshot_id": local_id, "chunks": checks["chunks"], "sources": checks["sources"],
                                                "failed_checks": checks["failed"], "unknown_checks": checks["unknown"],
                                                "check_statuses": {row["id"]: row["status"] for row in checks["checks"]},
                                                "source_fingerprints_observed": sum(source.sha256 is not None for source in local.sources),
                                                "raw_content_published": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "passed", "comparison": result["comparison"], "model_calls": 0}))


if __name__ == "__main__":
    main()
