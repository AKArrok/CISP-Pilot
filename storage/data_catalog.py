"""Knowledge snapshots and evidence-bound improvement records in the isolated platform DB."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import Field, model_validator

from storage import evaluation_store as store, evaluation_workspace as evaluation

SHA = r"^[0-9a-f]{64}$"


class Source(store.StrictModel):
    name: str = Field(min_length=1, max_length=500)
    locator: str = Field(default="", max_length=2000)
    sha256: str | None = Field(default=None, pattern=SHA)


class Snapshot(store.StrictModel):
    schema_version: Literal[1]
    namespace: str = Field(min_length=1, max_length=180)
    label: str = Field(min_length=1, max_length=180)
    purpose: Literal["knowledge"]
    visibility: Literal["private", "public", "synthetic"]
    artifacts: dict[str, str]
    manifest: dict
    processor: dict
    sources: list[Source] = Field(max_length=2000)
    chunks: list[dict] = Field(min_length=1, max_length=20000)
    vector_ids: list[str] | None = Field(default=None, max_length=20000)

    @model_validator(mode="after")
    def validate_snapshot(self):
        names = [source.name for source in self.sources]
        ids = []
        if len(set(names)) != len(names):
            raise ValueError("Duplicate source identity")
        for chunk in self.chunks:
            for key in ("id", "source", "text"):
                if not isinstance(chunk.get(key), str) or not chunk[key].strip():
                    raise ValueError("Knowledge chunks require id, source and nonempty text")
            if len(chunk["id"]) > 180 or len(chunk["text"]) > 200000 or chunk["source"] not in names:
                raise ValueError("Invalid chunk identity or unregistered source")
            if any(c in chunk["id"] for c in ("/", "\\", "\x00")):
                raise ValueError("Chunk identity cannot contain path separators")
            if any(chunk.get(key) is not None and not isinstance(chunk[key], str)
                   for key in ("parent_id", "parent_text", "child_text", "chunking_strategy", "source_url", "locator")):
                raise ValueError("Invalid optional chunk metadata")
            ids.append(chunk["id"])
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate chunk IDs")
        if "chunks" not in self.artifacts or any(len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
                                                  for value in self.artifacts.values()):
            raise ValueError("Artifact fingerprints must be SHA256")
        store.canonical(self.model_dump())
        return self


DDL = (
    "CREATE TABLE catalog_meta (version INTEGER NOT NULL)",
    """CREATE TABLE catalog_snapshots (id TEXT PRIMARY KEY, namespace TEXT NOT NULL, label TEXT NOT NULL,
         created_at TEXT NOT NULL, actor TEXT NOT NULL, payload TEXT NOT NULL, quality TEXT NOT NULL)""",
    """CREATE TABLE catalog_chunks (snapshot_id TEXT NOT NULL, id TEXT NOT NULL, source TEXT NOT NULL,
         text_sha TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(snapshot_id,id))""",
    "CREATE INDEX catalog_source ON catalog_chunks(snapshot_id,source)",
    """CREATE TABLE catalog_improvements (id TEXT PRIMARY KEY, actor TEXT NOT NULL, request_id TEXT NOT NULL,
         created_at TEXT NOT NULL, run_id TEXT NOT NULL, case_id TEXT NOT NULL, attempt INTEGER NOT NULL,
         snapshot_id TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(actor,request_id))""",
    """CREATE TABLE catalog_events (item_id TEXT NOT NULL, revision INTEGER NOT NULL, actor TEXT NOT NULL,
         request_id TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL,
         PRIMARY KEY(item_id,revision), UNIQUE(item_id,actor,request_id))""",
)


def ready(connection=None):
    if connection is None:
        with store.database() as connection:
            return ready(connection)
    row = connection.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_meta'").fetchone()
    if not row:
        return False
    if connection.execute("SELECT version FROM catalog_meta").fetchone()[0] != 1:
        raise ValueError("Unsupported data catalog version")
    return True


def require(connection):
    evaluation.require_v2(connection)
    if not ready(connection):
        raise ValueError("Run scripts/migrate_data_catalog.py explicitly first")


def migrate():
    with store.database() as connection:
        evaluation.require_v2(connection)
        if ready(connection):
            return {"version": 1, "upgraded": False}
        connection.execute("BEGIN IMMEDIATE")
        for statement in DDL:
            connection.execute(statement)
        connection.execute("INSERT INTO catalog_meta VALUES (1)")
    return {"version": 1, "upgraded": True}


def quality(bundle):
    chunks = bundle["chunks"]
    ids = {row["id"] for row in chunks}
    vectors = bundle["vector_ids"]
    hashes = [store.digest(row.get("child_text", row["text"])) for row in chunks]
    checks = []

    def add(name, passed, detail):
        checks.append({"id": name, "status": "unknown" if passed is None else "passed" if passed else "failed", "detail": detail})

    add("manifest_chunk_count", None if "chunk_count" not in bundle["manifest"] else bundle["manifest"]["chunk_count"] == len(chunks),
        {"observed": len(chunks), "declared": bundle["manifest"].get("chunk_count")})
    add("index_alignment", None if vectors is None else vectors == [row["id"] for row in chunks],
        {"chunks": len(chunks), "vector_rows": None if vectors is None else len(vectors), "includes_order": True})
    add("manifest_vector_count", None if vectors is None or "vector_count" not in bundle["manifest"] else len(vectors) == bundle["manifest"]["vector_count"],
        {"declared": bundle["manifest"].get("vector_count")})
    missing_parents = [row["id"] for row in chunks if row.get("parent_id") and row["parent_id"] not in ids
                       and not row.get("parent_text") and not (row.get("child_text") and row.get("chunking_strategy") == "structured_v1")]
    add("parent_evidence", not missing_parents, {"missing_ids": missing_parents[:100], "count": len(missing_parents)})
    missing_sources = [row["name"] for row in bundle["sources"] if row["sha256"] is None]
    add("original_source_fingerprints", None if missing_sources else True, {"unknown_sources": missing_sources})
    add("duplicate_text", len(set(hashes)) == len(hashes), {"duplicate_count": len(hashes) - len(set(hashes))})
    add("historical_processing_execution", None, "File observation only; historical parser/index execution not attested")
    return {"checks": checks, "chunks": len(chunks), "sources": len(bundle["sources"]),
            "failed": sum(row["status"] == "failed" for row in checks),
            "unknown": sum(row["status"] == "unknown" for row in checks), "model_calls": 0}


def import_snapshot(bundle, actor):
    payload = bundle.model_dump()
    identifier = store.digest(payload)
    with store.database() as connection:
        require(connection)
        created = connection.execute("INSERT OR IGNORE INTO catalog_snapshots VALUES (?,?,?,?,?,?,?)",
                                     (identifier, bundle.namespace, bundle.label, store.now(), actor, store.canonical(payload),
                                      store.canonical(quality(payload)))).rowcount == 1
        if created:
            connection.executemany("INSERT INTO catalog_chunks VALUES (?,?,?,?,?)", [
                (identifier, row["id"], row["source"], store.digest(row["text"]), store.canonical(row)) for row in bundle.chunks])
    return {"id": identifier, "created": created}


def snapshot(identifier, connection=None):
    if connection is None:
        with store.database() as connection:
            return snapshot(identifier, connection)
    require(connection)
    row = connection.execute("SELECT * FROM catalog_snapshots WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise KeyError("Snapshot missing")
    data = dict(row)
    data["payload"], data["quality"] = json.loads(row["payload"]), json.loads(row["quality"])
    if store.digest(data["payload"]) != identifier:
        raise ValueError("Snapshot integrity failure")
    return data


def snapshots():
    with store.database() as connection:
        require(connection)
        rows = connection.execute("SELECT id,namespace,label,created_at,quality FROM catalog_snapshots ORDER BY created_at DESC,id").fetchall()
    return [dict(row) | {"quality": json.loads(row["quality"])} for row in rows]


def chunks(identifier, query="", offset=0, limit=20):
    with store.database() as connection:
        snapshot(identifier, connection)
        condition = "snapshot_id=? AND (instr(id,?)>0 OR instr(source,?)>0 OR instr(payload,?)>0)"
        args = (identifier, query, query, query)
        total = connection.execute("SELECT COUNT(*) FROM catalog_chunks WHERE " + condition, args).fetchone()[0]
        rows = connection.execute("SELECT payload FROM catalog_chunks WHERE " + condition + " ORDER BY source,id LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
    return {"total": total, "items": [json.loads(row[0]) for row in rows], "offset": offset}


def chunk(identifier, chunk_id):
    data = snapshot(identifier)
    found = next((row for row in data["payload"]["chunks"] if row["id"] == chunk_id), None)
    if found is None:
        raise KeyError("Chunk missing")
    source = next(row for row in data["payload"]["sources"] if row["name"] == found["source"])
    parent = next((row for row in data["payload"]["chunks"] if row["id"] == found.get("parent_id")), None)
    if parent is None and found.get("parent_id") and found.get("child_text") and found.get("chunking_strategy") == "structured_v1":
        parent = {"id": found["parent_id"], "text": found["text"], "storage": "inline parent text per CISP structured_v1"}
    return {"chunk": found, "source": source, "parent": parent,
            "original_file_status": "fingerprint_recorded_not_served" if source["sha256"] else "unknown",
            "artifacts": data["payload"]["artifacts"], "processor": data["payload"]["processor"]}


def lineage(identifier, run_id, case_id, attempt=1):
    data = snapshot(identifier)["payload"]
    detail = evaluation.detail(run_id, case_id, attempt)
    run = store.get_run(run_id)["payload"]
    # The namespace-specific key must have been frozen in the original run, never inferred from names.
    recorded = run["artifacts"].get("knowledge:" + data["namespace"])
    binding = "hash_bound" if recorded == data["artifacts"]["chunks"] else "hash_mismatch" if recorded else "historical_version_unknown"
    matches = []
    # ponytail: bounded local scan; add a text index/async diagnostics when corpus size exceeds this workspace.
    for index, context in enumerate((detail["result"] or {}).get("contexts", [])):
        candidates = []
        for item in data["chunks"]:
            same_source = item["source"] == context.get("source")
            same_page = context.get("page") is None or item.get("page") == context["page"]
            exact = same_source and same_page and context.get("text") == item["text"]
            excerpt = same_source and same_page and isinstance(context.get("text"), str) and len(context["text"].strip()) >= 30 and context["text"] in item["text"]
            identity = context.get("id") == item["id"]
            locator = same_source and context.get("locator") is not None and context["locator"] == item.get("locator")
            if exact or excerpt or identity or locator:
                candidates.append({"chunk_id": item["id"], "source": item["source"], "page": item.get("page"),
                                   "match": "exact_text" if exact else "excerpt_candidate" if excerpt else "id_candidate" if identity else "locator_candidate",
                                   "verified": binding == "hash_bound" and exact})
        if len(candidates) > 1:
            for candidate in candidates:
                candidate["verified"] = False
        matches.append({"rank": index + 1, "context": context, "candidates": candidates[:100],
                        "candidate_count": len(candidates), "ambiguous": len(candidates) > 1})
    return {"snapshot_id": identifier, "run_id": run_id, "case_id": case_id, "attempt": attempt,
            "version_binding": binding, "case_version": detail["case_version"], "rubric_id": detail["rubric_id"],
            "answer_sha256": (detail["result"] or {}).get("answer_sha256"), "status": detail["status"],
            "matches": matches, "scope": "Candidate linkage is not proof of historical processing or root cause"}


def analysis(run_id, offset=0, limit=20):
    with store.database() as connection:
        require(connection)
        evaluation.experiment(connection, run_id)
        counts = [dict(row) for row in connection.execute("SELECT status,COUNT(*) AS count FROM eval_attempts WHERE run_id=? GROUP BY status", (run_id,))]
        metrics = [dict(row) for row in connection.execute("""SELECT dimension,id,evaluator_version,kind,status,COUNT(*) AS count
            FROM eval_scores WHERE run_id=? GROUP BY dimension,id,evaluator_version,kind,status ORDER BY dimension,id,status""", (run_id,))]
        total = connection.execute("SELECT COUNT(*) FROM eval_attempts WHERE run_id=? AND status!='passed'", (run_id,)).fetchone()[0]
        cases = [dict(row) for row in connection.execute("""SELECT a.case_id,a.attempt_no,a.status,c.question FROM eval_attempts a
            JOIN eval_experiments e ON e.run_id=a.run_id JOIN eval_case_versions c ON c.version_id=e.version_id AND c.case_id=a.case_id
            WHERE a.run_id=? AND a.status!='passed' ORDER BY a.case_id,a.attempt_no LIMIT ? OFFSET ?""", (run_id, limit, offset))]
        sources = [dict(row) for row in connection.execute("""WITH associations AS (
            SELECT DISTINCT a.case_id,a.attempt_no,a.status,json_extract(c.value,'$.source') AS source
            FROM eval_attempts a,json_each(a.result,'$.contexts') c WHERE a.run_id=? AND json_extract(c.value,'$.source') IS NOT NULL)
            SELECT source,status,COUNT(*) AS attempts FROM associations GROUP BY source,status ORDER BY source,status""", (run_id,))]
    return {"run_id": run_id, "attempt_counts": counts, "metrics": metrics, "failures": cases, "total": total,
            "offset": offset, "sources": sources,
            "scope": "Scores grouped by evaluator and dimension; source association is not causal attribution"}


class Improvement(store.StrictModel):
    request_id: str = Field(min_length=8, max_length=100)
    run_id: str
    case_id: str
    attempt: int = Field(ge=1, le=20)
    snapshot_id: str
    category: Literal["coverage", "parsing", "retrieval", "generation", "evidence", "unknown"]
    hypothesis: str = Field(min_length=1, max_length=4000)
    evidence: str = Field(min_length=1, max_length=4000)
    acceptance: str = Field(min_length=1, max_length=4000)
    chunk_ids: list[str] = Field(default_factory=list, max_length=30)


class Event(store.StrictModel):
    request_id: str = Field(min_length=8, max_length=100)
    revision: int = Field(ge=0)
    action: Literal["investigating", "ready_for_retest", "note", "retest"]
    note: str = Field(min_length=1, max_length=4000)
    candidate_run: str | None = None
    candidate_attempt: int = Field(default=1, ge=1, le=20)


def create_item(body, actor):
    bound = lineage(body.snapshot_id, body.run_id, body.case_id, body.attempt)
    available = {row["id"] for row in snapshot(body.snapshot_id)["payload"]["chunks"]}
    if set(body.chunk_ids) - available:
        raise ValueError("Unknown selected chunk")
    payload = body.model_dump() | {"frozen": {key: bound[key] for key in ("case_version", "rubric_id", "answer_sha256", "version_binding")}}
    identifier = store.digest({"actor": actor, "request_id": body.request_id})
    with store.database() as connection:
        require(connection)
        old = connection.execute("SELECT payload FROM catalog_improvements WHERE id=?", (identifier,)).fetchone()
        if old and old[0] != store.canonical(payload):
            raise ValueError("Idempotency request changed")
        connection.execute("INSERT OR IGNORE INTO catalog_improvements VALUES (?,?,?,?,?,?,?,?,?)",
                           (identifier, actor, body.request_id, store.now(), body.run_id, body.case_id, body.attempt, body.snapshot_id, store.canonical(payload)))
    return item(identifier)


def item(identifier):
    with store.database() as connection:
        require(connection)
        row = connection.execute("SELECT * FROM catalog_improvements WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("Improvement missing")
        events = [dict(event) | {"payload": json.loads(event["payload"])} for event in connection.execute(
            "SELECT * FROM catalog_events WHERE item_id=? ORDER BY revision", (identifier,))]
    return dict(row) | {"payload": json.loads(row["payload"]), "events": events, "revision": len(events),
                        "status": next((event["payload"]["action"] for event in reversed(events) if event["payload"]["action"] != "note"), "open")}


def items(offset=0, limit=20):
    with store.database() as connection:
        require(connection)
        total = connection.execute("SELECT COUNT(*) FROM catalog_improvements").fetchone()[0]
        rows = connection.execute("SELECT id FROM catalog_improvements ORDER BY created_at DESC,id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return {"total": total, "items": [item(row[0]) for row in rows], "offset": offset}


def append_event(identifier, body, actor):
    existing = item(identifier)
    payload = body.model_dump()
    retry = next((event for event in existing["events"] if event["actor"] == actor and event["request_id"] == body.request_id), None)
    if retry:
        if any(retry["payload"].get(key) != value for key, value in payload.items()):
            raise ValueError("Idempotency request changed")
        return existing
    if body.action == "retest":
        if not body.candidate_run or body.candidate_run == existing["run_id"]:
            raise ValueError("Retest requires a different recorded run")
        comparison = evaluation.comparison_detail(existing["run_id"], body.candidate_run, existing["case_id"], existing["attempt"], body.candidate_attempt)
        summary = evaluation.compare(existing["run_id"], body.candidate_run)
        before, after = comparison["left"], comparison["right"]
        if before is None or any(existing["payload"]["frozen"][key] != value for key, value in {
                "case_version": before["case_version"], "rubric_id": before["rubric_id"],
                "answer_sha256": (before["result"] or {}).get("answer_sha256")}.items()):
            raise ValueError("Baseline evidence changed since improvement creation")
        outcome = "incomparable" if not comparison["comparable"] else "insufficient_evidence" if not before or not after or "unknown" in {before["status"], after["status"]} else (
            "fixed" if before["status"] == "failed" and after["status"] == "passed" else "regressed" if before["status"] == "passed" and after["status"] == "failed" else "unchanged")
        payload["result"] = {"outcome": outcome, "suite_counts": summary["counts"], "criteria_match": summary["criteria_match"],
                             "candidate_answer_sha256": ((after or {}).get("result") or {}).get("answer_sha256"),
                             "independent_human_gold": False, "deployed": False}
    elif body.candidate_run:
        raise ValueError("Candidate run belongs only to retest events")
    with store.database() as connection:
        require(connection)
        connection.execute("BEGIN IMMEDIATE")
        count = connection.execute("SELECT COUNT(*) FROM catalog_events WHERE item_id=?", (identifier,)).fetchone()[0]
        retry = connection.execute("SELECT payload FROM catalog_events WHERE item_id=? AND actor=? AND request_id=?", (identifier, actor, body.request_id)).fetchone()
        if retry:
            if retry[0] != store.canonical(payload):
                raise ValueError("Idempotency request changed")
        elif count != body.revision:
            raise ValueError("Revision changed; reload before appending")
        else:
            connection.execute("INSERT INTO catalog_events VALUES (?,?,?,?,?,?)", (identifier, count + 1, actor, body.request_id, store.now(), store.canonical(payload)))
    return item(identifier)
