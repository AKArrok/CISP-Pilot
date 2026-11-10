"""Observe explicit local knowledge files; never index, call providers or read answer sets."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def package(chunks_path, manifest_path=None, vectors_path=None, source_map_path=None,
            namespace="cisp-knowledge", label="local observation", visibility="private"):
    from storage.data_catalog import Snapshot

    artifacts, observed = {}, {}

    def read(path, name):
        path = Path(path).resolve()
        raw = path.read_bytes()
        fingerprint = hashlib.sha256(raw).hexdigest()
        artifacts[name] = fingerprint
        observed[path] = fingerprint
        return json.loads(raw)

    rows = read(chunks_path, "chunks")
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not all(key in row for key in ("id", "source", "text")) for row in rows):
        raise ValueError("Expected a knowledge chunk list, not questions or answer records")
    manifest = read(manifest_path, "manifest") if manifest_path else {}
    vectors = read(vectors_path, "vector_metadata") if vectors_path else None
    mapping = read(source_map_path, "source_map") if source_map_path else {}
    if not isinstance(mapping, dict) or set(mapping) - {row["source"] for row in rows}:
        raise ValueError("Source map must name only sources present in this corpus")
    sources = []
    for name in sorted({row["source"] for row in rows}):
        entry = {"name": name, "locator": "", "sha256": None}
        urls = {row.get("source_url") for row in rows if row["source"] == name and isinstance(row.get("source_url"), str)}
        if len(urls) == 1:
            entry["locator"] = urls.pop()
        if name in mapping:
            path = Path(mapping[name])
            if not path.is_absolute():
                path = Path(source_map_path).resolve().parent / path
            path = path.resolve()
            # Only metadata of explicitly named originals is retained; no arbitrary file HTTP serving.
            fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
            observed[path] = fingerprint
            entry.update(locator=str(path), sha256=fingerprint)
        sources.append(entry)
    processor = {"capture": "local-file-observation-v1", "historical_execution": "unknown",
                 "current_code_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                         for name in ("data_ingest/parse_courses.py", "data_ingest/chunking.py", "data_ingest/build_index.py")},
                 "note": "Current code fingerprints do not prove which code produced historical files"}
    result = Snapshot.model_validate({"schema_version": 1, "namespace": namespace, "label": label, "purpose": "knowledge",
                                     "visibility": visibility, "artifacts": artifacts, "manifest": manifest, "processor": processor,
                                     "sources": sources, "chunks": rows,
                                     "vector_ids": None if vectors is None else [row["id"] for row in vectors]})
    if any(hashlib.sha256(path.read_bytes()).hexdigest() != fingerprint for path, fingerprint in observed.items()):
        raise ValueError("Input changed during capture; no snapshot produced")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--vector-metadata", type=Path)
    parser.add_argument("--source-map", type=Path, help="JSON: exact source name to explicit local original path")
    parser.add_argument("--namespace", default="cisp-knowledge")
    parser.add_argument("--label", required=True)
    parser.add_argument("--visibility", choices=("private", "public", "synthetic"), default="private")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bundle = package(args.chunks, args.manifest, args.vector_metadata, args.source_map, args.namespace, args.label, args.visibility)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(bundle.model_dump_json(indent=2))
    print(json.dumps({"output": str(args.output), "chunks": len(bundle.chunks), "sources": len(bundle.sources), "model_calls": 0}))


if __name__ == "__main__":
    main()
